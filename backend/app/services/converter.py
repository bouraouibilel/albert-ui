import os
import re
import uuid
import base64
import asyncio
try:
    import fitz
except ImportError:
    try:
        import pymupdf as fitz
    except ImportError:
        fitz = None
import docx
import openpyxl
from bs4 import BeautifulSoup
import datetime
import time
from typing import List, Tuple, Optional, Dict, Any
from app.core.config import settings
from app.services.albert_client import albert_client

# Import résilient du logger
try:
    from app.core.logger import log_event
except ImportError:
    try:
        from core.logger import log_event
    except ImportError:
        def log_event(category: str, message: str, level: str = "INFO"):
            print(f"[{category}] {message}")


class DocumentConverter:
    """
    Service de conversion universelle de documents (Word DOCX, PDF, XLSX, TXT, HTML) vers Markdown (.md)
    optimisé pour le RAG Albert API :
    - Détection intelligente des titres numérotés (1., 1.1, 1.1.1, Chapitre, Annexe) et styles Word.
    - Injection automatique de tags contextuels [DOC: ... | VERSION: ... | SECTION: ...] dans chaque section/paragraphe.
    - Protection des tableaux (dédoublonnage des cellules fusionnées Word, résumé exhaustif des clés en tête de tableau,
      et répétition des en-têtes de colonnes sur les grands tableaux multi-chunks).
    """

    # Regex pour détecter un titre numéroté (ex: "1. Introduction", "3.2.1 Liste des batchs", "CHAPITRE 2 : Flux")
    NUMBERED_HEADING_RE = re.compile(
        r"^(?:(?:CHAPITRE|ANNEXE|PARTIE|SECTION)\s+[A-Z0-9IVX]+[\s:\-\.]*|"
        r"(\d+(?:\.\d+){0,5})\.?\s+)"
        r"([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ0-9][^\n]{1,120})$"
    )

    @staticmethod
    def _sanitize_path_segment(name: str) -> str:
        """Nettoie une chaîne pour former un nom de dossier sûr pour le système de fichiers et les URLs."""
        clean = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', str(name or "")).strip('._-')
        return clean or "default"

    @staticmethod
    def extract_doc_metadata(filename: str) -> Tuple[str, str]:
        """
        Extrait un nom de document propre (sans UUID temporaire) et la version éventuelle
        (ex: 'PASRAU_SFD_Batchs_V29.3.docx' -> ('PASRAU_SFD_Batchs', 'V29.3')).
        """
        stem = os.path.splitext(os.path.basename(filename))[0]
        # Retirer un préfixe UUID 8 chars éventuel (ex: 75308fa5_NomDoc)
        stem = re.sub(r'^[0-9a-fA-F]{8}_', '', stem)

        # Chercher une version explicite : V29.3, v17.1, V3.0, 2027.1.0.0, v1, etc.
        ver_match = re.search(r'(?:[_\-\s]|^)(v\d+(?:\.\d+)*|\d{4}\.\d+(?:\.\d+)+)(?:[_\-\s\(]|$)', stem, re.IGNORECASE)
        if ver_match:
            version = ver_match.group(1).upper()
        else:
            ver_match_alt = re.search(r'(V\d+(?:\.\d+)*)', stem, re.IGNORECASE)
            version = ver_match_alt.group(1).upper() if ver_match_alt else "V1.0"

        return stem, version

    @staticmethod
    def _build_context_tag(doc_title: str, doc_version: str, section_path: str, extra: str = "") -> str:
        """
        Construit la balise de contexte injectée au début de chaque section/bloc
        pour que l'embedding vectoriel et la recherche lexicale BM25 puissent filtrer
        par nom de document, version et numéro de section.
        """
        sec = section_path.strip() if section_path else "Général"
        parts = [
            f"DOC: {doc_title}",
            f"VERSION: {doc_version}",
            f"SECTION: {sec}"
        ]
        if extra:
            parts.append(extra)
        tag_str = " | ".join(parts)
        return f"\n> 🏷️ **[{tag_str}]**\n"

    @staticmethod
    def _detect_heading_level(text: str, style_name: str = "", outline_lvl: Optional[int] = None) -> Tuple[Optional[int], str]:
        """
        Détermine si un paragraphe est un titre (et son niveau 1..4) en combinant :
        1. Le style Word (Heading 1..6, Titre 1..6, Title)
        2. Le niveau d'outline XML Word (w:outlineLvl)
        3. La numérotation hiérarchique dans le texte (1., 1.1, 1.1.1, Chapitre...)
        """
        clean_txt = text.strip()
        if not clean_txt or len(clean_txt) > 160:
            return None, clean_txt

        s_lower = (style_name or "").lower()

        # 1. Style Word explicite
        for lvl in range(1, 7):
            if f"heading {lvl}" in s_lower or f"titre {lvl}" in s_lower or s_lower == f"h{lvl}":
                return min(lvl, 4), clean_txt
        if s_lower in ["title", "titre"]:
            return 1, clean_txt
        if s_lower in ["subtitle", "sous-titre"]:
            return 2, clean_txt

        # 2. Niveau outline XML Word
        if outline_lvl is not None and 0 <= outline_lvl <= 5:
            return min(outline_lvl + 1, 4), clean_txt

        # 3. Détection par numérotation de titre (ex: "3.2.1 Liste des batchs")
        # Ne doit pas finir par un point-virgule ou être une phrase longue avec verbe conjugué et point final
        if not clean_txt.endswith((".", ";", ",")) or len(clean_txt) < 80:
            m = DocumentConverter.NUMBERED_HEADING_RE.match(clean_txt)
            if m:
                num_part = m.group(1)
                if num_part:
                    dots = num_part.count(".")
                    # Ignorer les dates ou nombres isolés (ex: "2026. ...")
                    first_num = int(num_part.split(".")[0])
                    if first_num <= 50:
                        return min(dots + 1, 4), clean_txt
                else:
                    # CHAPITRE / ANNEXE / PARTIE
                    return 1, clean_txt

        return None, clean_txt

    @staticmethod
    def _format_table_for_rag(
        rows: List[List[str]],
        doc_title: str,
        doc_version: str,
        section_path: str,
        max_rows_per_chunk: int = 18
    ) -> str:
        """
        Formate un tableau pour une récupération RAG optimale :
        1. Nettoie et dédoublonne les colonnes vides.
        2. Génère un bloc 'Synthèse exhaustive du tableau' listant toutes les clés (1ère et 2ème colonnes)
           afin qu'une question globale (ex: 'Lister tous les batchs') obtienne 100% des éléments en un seul chunk.
        3. Découpe les grands tableaux en lots de `max_rows_per_chunk` lignes en répétant systématiquement :
           - Le tag contextuel [DOC | VERSION | SECTION | TABLEAU]
           - L'en-tête des colonnes Markdown | Col1 | Col2 | ... |
        """
        if not rows:
            return ""

        # Normaliser le nombre de colonnes
        max_cols = max(len(r) for r in rows)
        if max_cols == 0:
            return ""

        norm_rows = []
        for r in rows:
            padded = [re.sub(r'\s+', ' ', str(c or '')).strip().replace('|', '/') for c in r] + [""] * (max_cols - len(r))
            if any(cell for cell in padded):
                norm_rows.append(padded)

        if not norm_rows:
            return ""

        header = norm_rows[0]
        # Si certaines colonnes d'en-tête sont vides, leur donner un nom générique
        header = [h if h else f"Col_{idx+1}" for idx, h in enumerate(header)]
        data_rows = norm_rows[1:]

        if not data_rows:
            return "\n| " + " | ".join(header) + " |\n| " + " | ".join(["---"] * len(header)) + " |\n"

        blocks = []
        sec_label = section_path or "Document"

        # 1. Générer un index récapitulatif compact si le tableau contient >= 3 lignes de données
        if len(data_rows) >= 3:
            summary_items = []
            for idx, r in enumerate(data_rows, 1):
                non_empty = [c for c in r if c]
                if not non_empty:
                    continue
                if len(non_empty) >= 2 and len(non_empty[1]) < 90:
                    summary_items.append(f"{non_empty[0]} ({non_empty[1]})")
                else:
                    summary_items.append(non_empty[0])

            if summary_items:
                tag = DocumentConverter._build_context_tag(
                    doc_title, doc_version, sec_label, extra=f"SYNTHÈSE TABLEAU ({len(data_rows)} entrées)"
                )
                cols_str = ", ".join(header)
                items_str = " ; ".join(summary_items)
                blocks.append(
                    f"{tag}"
                    f"**Récapitulatif complet du tableau de la section « {sec_label} »** "
                    f"({len(data_rows)} lignes, Colonnes : *{cols_str}*) :\n"
                    f"Liste complète des éléments : {items_str}.\n"
                )

        # 2. Découper le tableau en sous-tableaux de `max_rows_per_chunk` lignes avec répétition de l'en-tête
        total_parts = (len(data_rows) + max_rows_per_chunk - 1) // max_rows_per_chunk
        header_line = "| " + " | ".join(header) + " |"
        sep_line = "| " + " | ".join(["---"] * len(header)) + " |"

        for part_idx in range(total_parts):
            start_i = part_idx * max_rows_per_chunk
            chunk_rows = data_rows[start_i:start_i + max_rows_per_chunk]
            part_label = f"TABLEAU Partie {part_idx + 1}/{total_parts}" if total_parts > 1 else "TABLEAU"
            tag = DocumentConverter._build_context_tag(doc_title, doc_version, sec_label, extra=part_label)

            table_lines = [tag, header_line, sep_line]
            for r in chunk_rows:
                table_lines.append("| " + " | ".join(r) + " |")
            blocks.append("\n".join(table_lines) + "\n")

        return "\n\n".join(blocks) + "\n\n"

    @staticmethod
    def _extract_docx_table_rows(table) -> List[List[str]]:
        """
        Extrait les lignes d'un tableau Word (.docx) en dédoublonnant les cellules fusionnées horizontalement
        (python-docx répète le même élément XML _tc lorsque deux colonnes sont fusionnées).
        """
        rows = []
        for row in table.rows:
            row_cells = []
            last_tc = None
            for cell in row.cells:
                tc = cell._tc
                if tc is last_tc:
                    # Cellule fusionnée horizontalement : éviter de dupliquer tout le texte
                    continue
                last_tc = tc
                cell_text = re.sub(r'\s+', ' ', cell.text.strip())
                row_cells.append(cell_text)
            if any(c for c in row_cells):
                rows.append(row_cells)
        return rows

    @staticmethod
    async def convert_to_markdown(file_path: str, filename: str, collection_name: str = "") -> dict:
        ext = os.path.splitext(filename)[1].lower()
        doc_prefix = str(uuid.uuid4())[:8]
        t0 = time.time()

        doc_title, doc_version = DocumentConverter.extract_doc_metadata(filename)

        # Structure de répertoire pour les images : <collection_id_ou_nom>/<nom_document>
        col_folder = DocumentConverter._sanitize_path_segment(collection_name or "default")
        doc_base_name = os.path.splitext(filename)[0]
        doc_folder = DocumentConverter._sanitize_path_segment(doc_base_name)

        # Dossier physique : storage/images/<col_folder>/<doc_folder>/
        img_dir = os.path.join(settings.IMAGE_STORAGE_DIR, col_folder, doc_folder)
        os.makedirs(img_dir, exist_ok=True)

        # Préfixe d'URL : http://.../static/images/<col_folder>/<doc_folder>
        img_url_base = f"{settings.IMAGE_BASE_URL.rstrip('/')}/{col_folder}/{doc_folder}"

        log_event("CONVERTER", f"📄 Début conversion : '{filename}' (Doc: {doc_title}, Version: {doc_version}, Format: {ext})")

        raw_text = ""
        tables_count = 0
        pages_count = 1

        if ext in [".docx", ".doc"]:
            raw_text, tables_count = await DocumentConverter._convert_docx(
                file_path, doc_prefix, img_dir, img_url_base, doc_title, doc_version
            )
            # Estimation du nombre de pages Word (~3000 caractères par page)
            pages_count = max(1, round(len(raw_text) / 3000))
        elif ext == ".pdf":
            raw_text, pages_count, tables_count = await DocumentConverter._convert_pdf(
                file_path, doc_prefix, img_dir, img_url_base, doc_title, doc_version
            )
        elif ext in [".xlsx", ".xls"]:
            raw_text, tables_count = DocumentConverter._convert_xlsx(file_path, doc_title, doc_version)
        elif ext in [".html", ".htm"]:
            raw_text = DocumentConverter._convert_html(file_path, doc_title, doc_version)
        elif ext in [".txt", ".md"]:
            log_event("CONVERTER", f"📖 Lecture et structuration du fichier texte '{filename}'...")
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_content = f.read()
            raw_text = DocumentConverter._enrich_plain_or_md_text(raw_content, doc_title, doc_version)
        else:
            log_event("CONVERTER", f"❌ Format non supporté: {ext}", level="ERROR")
            raise ValueError(f"Format non supporté: {ext}")

        cleaned_text = DocumentConverter._clean_markdown_text(raw_text)

        metadata_header = (
            "---\n"
            f"title: \"{filename}\"\n"
            f"document_name: \"{doc_title}\"\n"
            f"version: \"{doc_version}\"\n"
            f"converted_at: \"{datetime.datetime.now().isoformat()}\"\n"
            f"source_format: \"{ext}\"\n"
            f"pages: {pages_count}\n"
            f"tables_detected: {tables_count}\n"
            f"target_collection: \"{collection_name}\"\n"
            "---\n\n"
            f"# Document : {doc_title} (Version {doc_version})\n"
            f"{DocumentConverter._build_context_tag(doc_title, doc_version, 'En-tête Document')}\n\n"
        )

        final_markdown = metadata_header + cleaned_text
        elapsed = round(time.time() - t0, 2)

        log_event("CONVERTER", f"✨ Conversion terminée pour '{filename}' en {elapsed}s | Pages: {pages_count}, Tableaux: {tables_count}, Caractères: {len(final_markdown)}")

        return {
            "markdown_content": final_markdown,
            "filename": filename,
            "doc_title": doc_title,
            "doc_version": doc_version,
            "pages_count": pages_count,
            "tables_count": tables_count,
            "char_count": len(final_markdown)
        }

    @staticmethod
    async def _convert_docx(
        file_path: str,
        doc_prefix: str,
        img_dir: str,
        img_url_base: str,
        doc_title: str,
        doc_version: str
    ) -> tuple[str, int]:
        """
        Conversion Word (.docx) haute fidélité pour le RAG :
        - Reconstruction de la hiérarchie des titres (Heading 1..6, Titre 1..6, w:outlineLvl, numérotation 1.2.3)
        - Fil d'Ariane (Breadcrumb) injecté sous forme de tag [DOC | VERSION | SECTION] dans chaque section
        - Répétition du tag de contexte dans les sections longues (> 1400 chars) pour qu'aucun chunk ne perde son contexte
        - Tableaux convertis avec synthèse exhaustive des éléments + répétition d'en-tête par bloc
        """
        doc = docx.Document(file_path)
        md_content = []
        tables_count = len(doc.tables)

        log_event("CONVERTER-WORD", f"🔍 Parcours structuré du document Word '{doc_title}' ({len(doc.paragraphs)} paragraphes, {tables_count} tableaux)...")
        img_counter = 0

        # Pile hiérarchique des titres : {level: "Titre"}
        heading_stack: Dict[int, str] = {}
        current_section_path = "Introduction"
        chars_since_last_tag = 0

        def get_breadcrumb() -> str:
            if not heading_stack:
                return "Introduction"
            sorted_lvls = sorted(heading_stack.keys())
            return " > ".join(heading_stack[l] for l in sorted_lvls)

        for element in doc.element.body:
            if element.tag.endswith('p'):
                p = docx.text.paragraph.Paragraph(element, doc)
                text = p.text.strip()
                if text:
                    style_name = p.style.name if p.style else ""
                    
                    # Chercher outlineLvl éventuel dans le XML du paragraphe
                    outline_lvl = None
                    try:
                        pPr = element.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr')
                        if pPr is not None:
                            out_el = pPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}outlineLvl')
                            if out_el is not None:
                                val = out_el.attrib.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val')
                                if val is not None and str(val).isdigit():
                                    outline_lvl = int(val)
                    except Exception:
                        pass

                    heading_lvl, clean_heading = DocumentConverter._detect_heading_level(text, style_name, outline_lvl)

                    if heading_lvl is not None:
                        # Mettre à jour la pile hiérarchique des titres
                        heading_stack[heading_lvl] = clean_heading
                        for k in list(heading_stack.keys()):
                            if k > heading_lvl:
                                del heading_stack[k]
                        current_section_path = get_breadcrumb()
                        chars_since_last_tag = 0

                        hashes = "#" * heading_lvl
                        context_tag = DocumentConverter._build_context_tag(doc_title, doc_version, current_section_path)
                        md_content.append(f"\n\n{hashes} {clean_heading}\n{context_tag}\n")
                    else:
                        # Vérifier si c'est une liste à puces / numérotée Word
                        is_list = False
                        s_low = style_name.lower()
                        if "list" in s_low or "puce" in s_low or "num" in s_low:
                            is_list = True
                        else:
                            try:
                                pPr = element.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr')
                                if pPr is not None and pPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr') is not None:
                                    is_list = True
                            except Exception:
                                pass

                        # Si la section est très longue (> 1400 chars sans tag), réinjecter un rappel de contexte
                        if chars_since_last_tag > 1400:
                            context_tag = DocumentConverter._build_context_tag(doc_title, doc_version, current_section_path, extra="Suite")
                            md_content.append(f"\n{context_tag}\n")
                            chars_since_last_tag = 0

                        if is_list and not text.startswith(("-", "*", "•")):
                            md_content.append(f"- {text}\n")
                        else:
                            md_content.append(f"\n{text}\n")
                        chars_since_last_tag += len(text)

                # Extraction des images intégrées au paragraphe
                for blip in element.xpath('.//a:blip'):
                    rId = blip.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                    if rId and rId in doc.part.rels:
                        rel = doc.part.rels[rId]
                        if "image" in rel.target_ref:
                            try:
                                raw_ext = os.path.splitext(rel.target_ref)[1].lower().lstrip('.')
                                is_vector = raw_ext in ['emf', 'wmf', 'svg']

                                image_blob = rel.target_part.blob
                                if len(image_blob) < 15000:
                                    continue

                                img_ext = raw_ext if raw_ext in ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'] else 'png'
                                img_mime = f"image/{'jpeg' if img_ext in ['jpg', 'jpeg'] else img_ext}"

                                img_counter += 1
                                img_filename = f"img_{img_counter}.{img_ext}"
                                img_file_path = os.path.join(img_dir, img_filename)
                                with open(img_file_path, "wb") as f_img:
                                    f_img.write(image_blob)

                                img_url = f"{img_url_base}/{img_filename}"
                                md_content.append(f"\n\n![Schéma Technique Word ({current_section_path})]({img_url})\n\n")

                                if not is_vector:
                                    img_b64 = base64.b64encode(image_blob).decode("utf-8")
                                    log_event("CONVERTER-WORD", f"🖼️ Image Word enregistrée : {img_filename} ({round(len(image_blob)/1024, 1)} KB)")

                                    try:
                                        vision_analysis = await asyncio.wait_for(
                                            albert_client.describe_image(img_b64, mime_type=img_mime),
                                            timeout=20.0
                                        )
                                        if vision_analysis:
                                            log_event("CONVERTER-WORD", f"✅ Diagramme UML Word transcrit en Mermaid.js")
                                            md_content.append(f"\n\n{vision_analysis}\n\n")
                                    except asyncio.TimeoutError:
                                        log_event("CONVERTER-WORD", f"⚠️ Timeout vision (20s) sur image Word {img_filename}", level="WARNING")
                                else:
                                    log_event("CONVERTER-WORD", f"🖼️ Image vectorielle ({raw_ext.upper()}) enregistrée : {img_filename}")
                            except Exception as e:
                                log_event("CONVERTER-WORD", f"❌ Erreur image Word: {e}", level="ERROR")

            elif element.tag.endswith('tbl'):
                table = docx.table.Table(element, doc)
                rows = DocumentConverter._extract_docx_table_rows(table)
                if rows:
                    formatted_table = DocumentConverter._format_table_for_rag(
                        rows=rows,
                        doc_title=doc_title,
                        doc_version=doc_version,
                        section_path=current_section_path
                    )
                    md_content.append(formatted_table)
                    chars_since_last_tag = 0

        return "".join(md_content), tables_count

    @staticmethod
    async def _convert_pdf(
        file_path: str,
        doc_prefix: str,
        img_dir: str,
        img_url_base: str,
        doc_title: str,
        doc_version: str
    ) -> tuple[str, int, int]:
        """
        Conversion PDF avec extraction structurée des tableaux en Markdown, détection des titres numérotés
        et injection des tags contextuels [DOC | VERSION | SECTION].
        """
        doc = fitz.open(file_path)
        pages_count = len(doc)
        tables_count = 0
        md_content = []
        current_section_path = "Page 1"

        log_event("CONVERTER-PDF", f"🔍 Ouverture du PDF '{doc_title}' ({pages_count} pages)...")

        for page_num in range(pages_count):
            page = doc[page_num]
            page_label = f"Page {page_num + 1}"
            md_content.append(f"\n\n## {page_label}\n")
            md_content.append(DocumentConverter._build_context_tag(doc_title, doc_version, f"{current_section_path} ({page_label})"))

            # 1. Extraire proprement les tableaux de la page
            tabs = page.find_tables()
            if tabs and tabs.tables:
                found_tab_len = len(tabs.tables)
                tables_count += found_tab_len
                log_event("CONVERTER-PDF", f"📊 Page {page_num + 1}: {found_tab_len} tableau(x) détecté(s) et converti(s)")
                for tab in tabs.tables:
                    try:
                        extracted = tab.extract()
                        if extracted:
                            clean_rows = [
                                [str(c or "").replace("\n", " ").strip() for c in r]
                                for r in extracted if any(c for c in r)
                            ]
                            if clean_rows:
                                md_content.append(
                                    DocumentConverter._format_table_for_rag(
                                        clean_rows, doc_title, doc_version, f"{current_section_path} ({page_label})"
                                    )
                                )
                    except Exception:
                        pass

            # 2. Extraire le texte de la page et détecter les titres numérotés
            page_text = page.get_text("text").strip()
            if page_text:
                for line in page_text.splitlines():
                    s_line = line.strip()
                    if not s_line:
                        md_content.append("\n")
                        continue
                    lvl, clean_h = DocumentConverter._detect_heading_level(s_line)
                    if lvl is not None:
                        current_section_path = clean_h
                        hashes = "#" * lvl
                        tag = DocumentConverter._build_context_tag(doc_title, doc_version, current_section_path)
                        md_content.append(f"\n{hashes} {clean_h}\n{tag}\n")
                    else:
                        md_content.append(f"{s_line}\n")

            # 3. Images & Schémas techniques
            image_list = page.get_images(full=True)
            diagram_count = 0
            for img_index, img_info in enumerate(image_list):
                if diagram_count >= 2:
                    break
                try:
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    img_ext = base_image["ext"]

                    if len(image_bytes) < 15000:
                        continue

                    img_filename = f"img_p{page_num+1}_{img_index+1}.{img_ext}"
                    img_file_path = os.path.join(img_dir, img_filename)
                    with open(img_file_path, "wb") as f_img:
                        f_img.write(image_bytes)

                    img_url = f"{img_url_base}/{img_filename}"
                    md_content.append(f"\n\n![Schéma Technique Page {page_num+1}]({img_url})\n\n")

                    img_b64 = base64.b64encode(image_bytes).decode("utf-8")
                    img_mime = f"image/{'jpeg' if img_ext.lower() in ['jpg', 'jpeg'] else img_ext.lower()}"
                    log_event("CONVERTER-PDF", f"🖼️ Image enregistrée : {img_filename} ({round(len(image_bytes)/1024, 1)} KB)")

                    try:
                        vision_analysis = await asyncio.wait_for(
                            albert_client.describe_image(img_b64, mime_type=img_mime),
                            timeout=20.0
                        )
                        if vision_analysis:
                            log_event("CONVERTER-PDF", f"✅ Diagramme UML transcrit en Mermaid.js")
                            md_content.append(f"\n\n{vision_analysis}\n\n")
                    except asyncio.TimeoutError:
                        log_event("CONVERTER-PDF", f"⚠️ Timeout vision (20s) sur image {img_filename}", level="WARNING")

                    diagram_count += 1
                except Exception as e:
                    log_event("CONVERTER-PDF", f"❌ Erreur image page {page_num + 1}: {e}", level="ERROR")

        return "".join(md_content), pages_count, tables_count

    @staticmethod
    def _convert_xlsx(file_path: str, doc_title: str = "Excel", doc_version: str = "V1.0") -> tuple[str, int]:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        md_content = []
        tables_count = len(wb.sheetnames)

        log_event("CONVERTER-EXCEL", f"🔍 Traitement des {tables_count} feuille(s) Excel...")

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            md_content.append(f"\n# Feuille: {sheet_name}\n")
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue

            clean_rows = []
            for row in rows:
                if any(cell is not None for cell in row):
                    clean_rows.append([str(c or "").strip().replace("\n", " ") for c in row])

            if clean_rows:
                md_content.append(
                    DocumentConverter._format_table_for_rag(
                        clean_rows, doc_title, doc_version, f"Feuille {sheet_name}"
                    )
                )

        return "".join(md_content), tables_count

    @staticmethod
    def _convert_html(file_path: str, doc_title: str = "HTML", doc_version: str = "V1.0") -> str:
        log_event("CONVERTER-HTML", f"🔍 Extraction du contenu HTML...")
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f.read(), "html.parser")

        for element in soup(["script", "style", "nav", "footer"]):
            element.decompose()

        md_content = []
        current_section = "Introduction"
        for elem in soup.find_all(["h1", "h2", "h3", "h4", "p", "table"]):
            if elem.name in ["h1", "h2", "h3", "h4"]:
                level = int(elem.name[1])
                current_section = elem.get_text().strip()
                tag = DocumentConverter._build_context_tag(doc_title, doc_version, current_section)
                md_content.append(f"\n{'#' * level} {current_section}\n{tag}\n")
            elif elem.name == "p":
                text = elem.get_text().strip()
                if text:
                    md_content.append(f"\n{text}\n")
            elif elem.name == "table":
                rows = []
                for tr in elem.find_all("tr"):
                    cells = [td.get_text().strip().replace("\n", " ") for td in tr.find_all(["td", "th"])]
                    if cells:
                        rows.append(cells)
                if rows:
                    md_content.append(
                        DocumentConverter._format_table_for_rag(rows, doc_title, doc_version, current_section)
                    )

        return "".join(md_content)

    @staticmethod
    def _enrich_plain_or_md_text(raw_content: str, doc_title: str, doc_version: str) -> str:
        """
        Ajoute les tags de contexte [DOC | VERSION | SECTION] aux fichiers .txt ou .md existants
        s'ils n'en possèdent pas déjà.
        """
        if f"DOC: {doc_title}" in raw_content:
            return raw_content

        lines = raw_content.splitlines()
        out = []
        current_sec = "Introduction"
        for line in lines:
            s = line.strip()
            if s.startswith("#"):
                current_sec = s.lstrip("#").strip()
                out.append(line)
                out.append(DocumentConverter._build_context_tag(doc_title, doc_version, current_sec))
            else:
                lvl, clean_h = DocumentConverter._detect_heading_level(s)
                if lvl is not None:
                    current_sec = clean_h
                    out.append(f"\n{'#' * lvl} {clean_h}")
                    out.append(DocumentConverter._build_context_tag(doc_title, doc_version, current_sec))
                else:
                    out.append(line)
        return "\n".join(out)

    @staticmethod
    def _clean_markdown_text(text: str) -> str:
        lines = text.splitlines()
        cleaned_lines = []
        empty_count = 0
        for line in lines:
            if not line.strip():
                empty_count += 1
                if empty_count <= 2:
                    cleaned_lines.append("")
            else:
                empty_count = 0
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines)

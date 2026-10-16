from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List, Tuple
import os
import uuid
import json
import zipfile
import io
import re
from pathlib import Path

from app.core.config import settings
from app.services.converter import DocumentConverter
from app.services.albert_client import albert_client
from app.api.collections import get_local_collections, save_local_collections

router = APIRouter(prefix="/documents", tags=["Documents & Conversion"])

LOCAL_DOCUMENTS_META_FILE = os.path.join(settings.CONVERTED_DIR, "documents_meta.json")

def get_local_docs() -> List[dict]:
    if os.path.exists(LOCAL_DOCUMENTS_META_FILE):
        try:
            with open(LOCAL_DOCUMENTS_META_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_local_docs(docs: List[dict]):
    with open(LOCAL_DOCUMENTS_META_FILE, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)

class IngestRequest(BaseModel):
    collection_id: str
    filename: str
    markdown_content: str
    original_format: Optional[str] = ".md"
    chunk_size: Optional[int] = 2048
    chunk_overlap: Optional[int] = 256
    preset_separators: Optional[str] = "markdown"

class CompareDocumentsRequest(BaseModel):
    doc_a_id: str
    doc_a_name: Optional[str] = None
    doc_b_id: str
    doc_b_name: Optional[str] = None
    use_llm_summary: Optional[bool] = True

class DownloadPackRequest(BaseModel):
    filename: str
    markdown_content: str

@router.get("/")
async def list_documents(collection_id: Optional[str] = Query(None, description="ID de la collection cible")):
    """
    Récupère la liste des documents d'une collection directement depuis Albert API
    (et fusionne les métadonnées locales).
    """
    remote_docs = await albert_client.list_documents(collection_id=collection_id, limit=100)
    local_docs = get_local_docs()
    
    if collection_id:
        local_docs = [d for d in local_docs if str(d.get("collection_id")) == str(collection_id)]

    merged = {}
    
    # 1. Traiter les documents renvoyés par Albert API
    if isinstance(remote_docs, list):
        for r in remote_docs:
            if isinstance(r, dict):
                doc_key = str(r.get("id") or r.get("name") or "")
                if doc_key:
                    merged[doc_key] = r

    # 2. Fusionner avec les métadonnées locales
    for d in local_docs:
        if isinstance(d, dict):
            doc_key = str(d.get("id") or d.get("name") or "")
            if doc_key:
                if doc_key not in merged:
                    merged[doc_key] = d
                else:
                    merged[doc_key].update(d)

    return list(merged.values())

@router.get("/{document_id}/markdown")
async def get_document_markdown(
    document_id: str,
    filename: Optional[str] = Query(None, description="Nom ou fragment de nom de fichier")
):
    if not isinstance(filename, str):
        filename = None

    converted_dir = settings.CONVERTED_DIR
    target_md_path = None
    target_filename = None
    original_filename = filename or document_id

    # 1. Vérifier si un nom de fichier direct est donné
    if filename:
        p = os.path.join(converted_dir, filename)
        if os.path.exists(p) and os.path.isfile(p):
            target_md_path = p
            target_filename = filename
        elif not filename.endswith(".md"):
            p_md = os.path.join(converted_dir, f"{filename}.md")
            if os.path.exists(p_md) and os.path.isfile(p_md):
                target_md_path = p_md
                target_filename = f"{filename}.md"

    # 2. Chercher dans documents_meta.json
    if not target_md_path:
        docs = get_local_docs()
        for d in docs:
            if str(d.get("id")) == str(document_id) or d.get("filename") == filename or d.get("name") == filename:
                md_name = d.get("filename")
                if md_name:
                    p = os.path.join(converted_dir, md_name)
                    if os.path.exists(p):
                        target_md_path = p
                        target_filename = md_name
                        original_filename = d.get("name", original_filename)
                        break

    # 3. Scan par suffixe dans storage/converted/
    if not target_md_path and os.path.exists(converted_dir):
        stem = Path(filename or document_id).stem
        if stem.startswith("doc_"):
            stem = stem[4:]
        stem_md = stem if stem.endswith(".md") else f"{stem}.md"
        for fname in os.listdir(converted_dir):
            if fname.endswith(".md"):
                if fname == stem_md or fname.endswith(f"_{stem}.md") or (stem and len(stem) > 3 and stem in fname):
                    target_md_path = os.path.join(converted_dir, fname)
                    target_filename = fname
                    break

    if not target_md_path or not os.path.exists(target_md_path):
        raise HTTPException(
            status_code=404, 
            detail=f"Fichier Markdown introuvable pour le document '{document_id}' (nom: '{filename}')."
        )

    try:
        with open(target_md_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        stat = os.stat(target_md_path)
        return {
            "status": "success",
            "document_id": document_id,
            "filename": target_filename or os.path.basename(target_md_path),
            "original_filename": original_filename,
            "markdown_content": content,
            "char_count": len(content),
            "file_size": stat.st_size,
            "mtime": stat.st_mtime
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de lecture du fichier Markdown: {str(e)}")

@router.get("/{document_id}/chunks")
async def get_document_chunks(document_id: str, limit: int = Query(50, description="Nombre de chunks à retourner")):
    """
    Récupère les extraits/morceaux (chunks) d'un document référencé dans Albert API.
    """
    chunks = await albert_client.get_document_chunks(document_id, limit=limit)
    return chunks

@router.post("/convert")
async def convert_document(
    file: UploadFile = File(...),
    collection_name: Optional[str] = Form("")
):
    """
    Étape cruciale 1: Reçoit un document brut (PDF, DOCX, XLSX, HTML), extrait les textes, tableaux
    et images/schémas techniques, effectue l'analyse multimodale avec Albert API (description + Mermaid.js)
    et retourne le Markdown enrichi pour prévisualisation et édition.
    """
    try:
        temp_id = str(uuid.uuid4())[:8]
        raw_filename = f"{temp_id}_{file.filename}"
        raw_file_path = os.path.join(settings.UPLOAD_DIR, raw_filename)
        
        content = await file.read()
        with open(raw_file_path, "wb") as f:
            f.write(content)

        conversion_result = await DocumentConverter.convert_to_markdown(
            file_path=raw_file_path,
            filename=file.filename,
            collection_name=collection_name or ""
        )

        md_filename = f"{os.path.splitext(file.filename)[0]}.md"
        converted_file_path = os.path.join(settings.CONVERTED_DIR, f"{temp_id}_{md_filename}")
        
        with open(converted_file_path, "w", encoding="utf-8") as f:
            f.write(conversion_result["markdown_content"])

        return {
            "status": "converted",
            "doc_id": temp_id,
            "original_filename": file.filename,
            "md_filename": md_filename,
            "doc_title": conversion_result.get("doc_title", ""),
            "doc_version": conversion_result.get("doc_version", "N/A"),
            "markdown_content": conversion_result["markdown_content"],
            "pages_count": conversion_result["pages_count"],
            "tables_count": conversion_result["tables_count"],
            "char_count": conversion_result["char_count"]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erreur de conversion: {str(e)}")

@router.post("/ingest")
async def ingest_document_to_albert(payload: IngestRequest):
    """
    Étape cruciale 2: Envoie le document Markdown validé/édité vers la collection Albert API
    avec paramètres de découpage optimisés (chunk_size=2048, chunk_overlap=256, preset_separators='markdown').
    """
    temp_id = str(uuid.uuid4())[:8]
    md_filename = payload.filename if payload.filename.endswith(".md") else f"{payload.filename}.md"
    file_path = os.path.join(settings.CONVERTED_DIR, f"{temp_id}_{md_filename}")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(payload.markdown_content)

    chunk_size = payload.chunk_size or 2048
    chunk_overlap = payload.chunk_overlap if payload.chunk_overlap is not None else 256
    preset_separators = payload.preset_separators if payload.preset_separators is not None else "markdown"

    result = await albert_client.upload_document(
        collection_id=payload.collection_id,
        file_path=file_path,
        filename=md_filename,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        preset_separators=preset_separators
    )

    doc_meta_extracted = DocumentConverter.extract_doc_metadata(payload.filename)

    docs = get_local_docs()
    new_doc_meta = {
        "id": result.get("id", temp_id),
        "filename": f"{temp_id}_{md_filename}",
        "name": payload.filename,
        "doc_title": doc_meta_extracted["doc_title"],
        "doc_version": doc_meta_extracted["doc_version"],
        "collection_id": payload.collection_id,
        "original_format": payload.original_format,
        "size_chars": len(payload.markdown_content),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "status": "indexed_in_albert",
        "ingested_at": result.get("created_at")
    }
    docs.append(new_doc_meta)
    save_local_docs(docs)

    cols = get_local_collections()
    for col in cols:
        if str(col.get("id")) == str(payload.collection_id) or col.get("name") == payload.collection_id:
            col["document_count"] = col.get("document_count", 0) + 1
    save_local_collections(cols)

    return {
        "status": "success",
        "message": f"Document Markdown indexé avec succès dans Albert API (Chunk: {chunk_size} / Overlap: {chunk_overlap})",
        "document_metadata": new_doc_meta
    }


def _parse_markdown_sections(md_content: str) -> List[dict]:
    """
    Découpe un document Markdown en sections structurées (basées sur les titres #, ##, ###...)
    en nettoyant les balises de métadonnées [DOC: ... | VERSION: ...] pour permettre une
    comparaison purement fonctionnelle entre deux versions d'un même document.
    """
    lines = md_content.splitlines()
    sections = []
    current_title = "Préambule / En-tête"
    current_number = "0"
    current_level = 1
    current_lines = []

    heading_regex = re.compile(r'^(#{1,6})\s+(.+)$')
    num_regex = re.compile(r'^((?:chapitre|annexe)\s+\d+|\d+(?:\.\d+)*)\b[\s\.\-\:]*\s*(.*)$', re.IGNORECASE)

    def flush_section(title: str, number: str, level: int, body_lines: List[str]):
        # Filtrer les lignes purement techniques (tags de contexte, séparateurs, ancres d'en-tête)
        cleaned = []
        for ln in body_lines:
            s = ln.strip()
            if s.startswith("> 🏷️ **[DOC:") or s.startswith("- **Document de référence"):
                continue
            if s.startswith("- **Fichier source") or s.startswith("- **Version identifiée") or s.startswith("- **Format d'origine"):
                continue
            if s == "---":
                continue
            cleaned.append(ln)
        body_text = "\n".join(cleaned).strip()
        if not body_text and title == "Préambule / En-tête":
            return
        has_table = "|" in body_text and "| ---" in body_text
        sections.append({
            "number": number,
            "title": title,
            "level": level,
            "content": body_text,
            "has_table": has_table
        })

    for line in lines:
        m = heading_regex.match(line.strip())
        if m:
            flush_section(current_title, current_number, current_level, current_lines)
            current_level = len(m.group(1))
            raw_heading = m.group(2).strip()
            # Ignorer le titre H1 global du document s'il commence par 📄 Document :
            if raw_heading.startswith("📄 Document :"):
                current_title = "Préambule / En-tête"
                current_number = "0"
                current_lines = []
                continue
            current_title = raw_heading
            nm = num_regex.match(raw_heading)
            if nm:
                current_number = nm.group(1).upper().rstrip('.')
            else:
                current_number = ""
            current_lines = []
        else:
            current_lines.append(line)

    flush_section(current_title, current_number, current_level, current_lines)
    return sections


def _normalize_section_key(sec: dict) -> str:
    """Clé d'appariement d'une section entre deux versions (numéro + titre normalisé ou titre seul)."""
    title_clean = re.sub(r'^((?:chapitre|annexe)\s+\d+|\d+(?:\.\d+)*)\b[\s\.\-\:]*', '', sec["title"], flags=re.IGNORECASE).strip().lower()
    title_clean = re.sub(r'[^a-z0-9àâäéèêëîïôöùûüç]+', ' ', title_clean).strip()
    if sec["number"] and sec["number"] != "0":
        return f"{sec['number']}::{title_clean}"
    return title_clean or sec["title"].lower()


@router.post("/compare")
async def compare_documents(payload: CompareDocumentsRequest):
    """
    Compare deux versions d'un document (section par section et tableau par tableau)
    et génère une synthèse des ajouts, suppressions et modifications.
    """
    import difflib

    async def load_doc_text(doc_id: str, doc_name: Optional[str]) -> Tuple[str, str]:
        try:
            res = await get_document_markdown(document_id=doc_id, filename=doc_name)
            return res.get("markdown_content", ""), res.get("original_filename") or doc_name or doc_id
        except Exception:
            # Fallback : reconstruire depuis les chunks Albert API
            chunks = await albert_client.get_document_chunks(doc_id, limit=200)
            if chunks:
                text = "\n\n".join(c.get("content", "") for c in chunks if c.get("content"))
                return text, doc_name or doc_id
            raise HTTPException(status_code=404, detail=f"Impossible de récupérer le contenu du document '{doc_name or doc_id}'.")

    md_a, name_a = await load_doc_text(payload.doc_a_id, payload.doc_a_name)
    md_b, name_b = await load_doc_text(payload.doc_b_id, payload.doc_b_name)

    sections_a = _parse_markdown_sections(md_a)
    sections_b = _parse_markdown_sections(md_b)

    # Indexation des sections avec gestion des clés et repli par numéro ou titre
    map_a = {}
    for idx, s in enumerate(sections_a):
        key = _normalize_section_key(s) or f"sec_{idx}"
        if key in map_a:
            key = f"{key}_{idx}"
        s["_key"] = key
        map_a[key] = s

    map_b = {}
    for idx, s in enumerate(sections_b):
        key = _normalize_section_key(s) or f"sec_{idx}"
        if key in map_b:
            key = f"{key}_{idx}"
        s["_key"] = key
        map_b[key] = s

    # Second passage d'appariement : si une section a changé légèrement de numéro ou de titre
    matched_a_keys = set()
    matched_b_keys = set()
    pairs = []

    # 1. Appariement exact sur _key
    for k_b, sec_b in map_b.items():
        if k_b in map_a:
            pairs.append((map_a[k_b], sec_b))
            matched_a_keys.add(k_b)
            matched_b_keys.add(k_b)

    # 2. Appariement sur le numéro de paragraphe exact (ex: 3.2) ou sur le titre sans numéro
    for k_b, sec_b in map_b.items():
        if k_b in matched_b_keys:
            continue
        num_b = sec_b.get("number")
        title_b_norm = re.sub(r'^((?:chapitre|annexe)\s+\d+|\d+(?:\.\d+)*)\b[\s\.\-\:]*', '', sec_b["title"], flags=re.IGNORECASE).strip().lower()
        for k_a, sec_a in map_a.items():
            if k_a in matched_a_keys:
                continue
            num_a = sec_a.get("number")
            title_a_norm = re.sub(r'^((?:chapitre|annexe)\s+\d+|\d+(?:\.\d+)*)\b[\s\.\-\:]*', '', sec_a["title"], flags=re.IGNORECASE).strip().lower()
            if (num_b and num_b != "0" and num_b == num_a) or (title_b_norm and len(title_b_norm) > 4 and title_b_norm == title_a_norm):
                pairs.append((sec_a, sec_b))
                matched_a_keys.add(k_a)
                matched_b_keys.add(k_b)
                break

    added_sections = [
        {"title": s["title"], "number": s["number"], "has_table": s["has_table"], "preview": s["content"][:500]}
        for k, s in map_b.items() if k not in matched_b_keys
    ]
    removed_sections = [
        {"title": s["title"], "number": s["number"], "has_table": s["has_table"], "preview": s["content"][:500]}
        for k, s in map_a.items() if k not in matched_a_keys
    ]

    modified_sections = []
    unchanged_count = 0

    for sec_a, sec_b in pairs:
        norm_a = re.sub(r'\s+', ' ', sec_a["content"]).strip()
        norm_b = re.sub(r'\s+', ' ', sec_b["content"]).strip()
        if norm_a == norm_b and sec_a["title"].strip() == sec_b["title"].strip():
            unchanged_count += 1
        else:
            lines_a = [ln for ln in sec_a["content"].splitlines() if ln.strip()]
            lines_b = [ln for ln in sec_b["content"].splitlines() if ln.strip()]
            diff_gen = list(difflib.unified_diff(
                lines_a, lines_b,
                fromfile=f"Version A ({name_a})",
                tofile=f"Version B ({name_b})",
                lineterm="",
                n=1
            ))
            # Garder les lignes significatives du diff (+ / -)
            diff_Changes = [ln for ln in diff_gen[2:] if ln.startswith("+") or ln.startswith("-") or ln.startswith("@@")][:40]
            similarity = round(difflib.SequenceMatcher(None, norm_a, norm_b).ratio() * 100, 1)

            modified_sections.append({
                "title_a": sec_a["title"],
                "title_b": sec_b["title"],
                "number": sec_b["number"] or sec_a["number"],
                "has_table": sec_a["has_table"] or sec_b["has_table"],
                "similarity_pct": similarity,
                "diff_lines": diff_Changes,
                "excerpt_a": sec_a["content"][:600],
                "excerpt_b": sec_b["content"][:600]
            })

    # Synthèse IA optionnelle via Albert API
    llm_summary = None
    if payload.use_llm_summary and (added_sections or removed_sections or modified_sections):
        diff_digest = []
        if added_sections:
            diff_digest.append("SECTIONS AJOUTÉES DANS LA NOUVELLE VERSION :\n" + "\n".join(f"- {s['title']}: {s['preview'][:200]}" for s in added_sections[:10]))
        if removed_sections:
            diff_digest.append("SECTIONS SUPPRIMÉES :\n" + "\n".join(f"- {s['title']}" for s in removed_sections[:10]))
        if modified_sections:
            mod_txt = []
            for m in modified_sections[:12]:
                changes_str = "\n  ".join(m["diff_lines"][:12])
                mod_txt.append(f"- Section '{m['title_b']}' (similarité {m['similarity_pct']}%) :\n  {changes_str}")
            diff_digest.append("SECTIONS MODIFIÉES :\n" + "\n".join(mod_txt))

        prompt_text = (
            f"Compare ces deux versions de documents techniques :\n"
            f"- Version A (Référence) : {name_a}\n"
            f"- Version B (Cible) : {name_b}\n\n"
            f"Voici les écarts détectés section par section :\n\n"
            + "\n\n".join(diff_digest)
            + "\n\nFais une synthèse structurée en français (points clés, impacts sur les tableaux/batchs/règles de gestion, ajouts et suppressions)."
        )
        llm_summary = await albert_client.generate_text(
            system_prompt="Tu es un expert en analyse documentaire et spécifications techniques (SFD). Tu rédiges des synthèses d'écarts claires, précises et factuelles en français.",
            user_prompt=prompt_text,
            temperature=0.15
        )

    if not llm_summary:
        if not added_sections and not removed_sections and not modified_sections:
            llm_summary = "Les deux documents sont fonctionnellement identiques (aucun écart détecté sur les paragraphes ou tableaux)."
        else:
            llm_summary = (
                f"Comparaison terminée entre **{name_a}** et **{name_b}** : "
                f"**{len(modified_sections)}** section(s) modifiée(s), "
                f"**{len(added_sections)}** section(s) ajoutée(s), "
                f"**{len(removed_sections)}** section(s) supprimée(s) et "
                f"**{unchanged_count}** section(s) inchangée(s)."
            )

    return {
        "status": "success",
        "doc_a": {"id": payload.doc_a_id, "name": name_a, "sections_count": len(sections_a)},
        "doc_b": {"id": payload.doc_b_id, "name": name_b, "sections_count": len(sections_b)},
        "stats": {
            "added_count": len(added_sections),
            "removed_count": len(removed_sections),
            "modified_count": len(modified_sections),
            "unchanged_count": unchanged_count
        },
        "added_sections": added_sections,
        "removed_sections": removed_sections,
        "modified_sections": modified_sections,
        "summary": llm_summary
    }


@router.post("/download-pack")
async def download_document_pack(payload: DownloadPackRequest):
    """
    Génère et télécharge un pack d'archive .ZIP contenant :
    1. Le fichier Markdown (.md) avec des liens d'images relatifs autonomes (images/...).
    2. Le dossier 'images/' contenant toutes les images physiques référencées dans le document.
    """
    clean_name = os.path.splitext(payload.filename)[0]
    md_filename = f"{clean_name}.md"
    zip_filename = f"{clean_name}_pack.zip"

    # Trouver toutes les images référencées dans le markdown_content: ![alt](chemin_ou_url)
    img_matches = re.findall(r'!\[.*?\]\((.*?)\)', payload.markdown_content)

    zip_buffer = io.BytesIO()
    archive_markdown = payload.markdown_content

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        added_images = set()
        for img_path in img_matches:
            base_img_name = os.path.basename(img_path)
            
            # Recherche de l'image physique dans les sous-dossiers de settings.IMAGE_STORAGE_DIR
            local_img_file = None
            if os.path.exists(os.path.join(settings.IMAGE_STORAGE_DIR, base_img_name)):
                local_img_file = os.path.join(settings.IMAGE_STORAGE_DIR, base_img_name)
            else:
                for root, dirs, files in os.walk(settings.IMAGE_STORAGE_DIR):
                    if base_img_name in files:
                        local_img_file = os.path.join(root, base_img_name)
                        break

            if local_img_file and os.path.exists(local_img_file):
                if base_img_name not in added_images:
                    zf.write(local_img_file, arcname=f"images/{base_img_name}")
                    added_images.add(base_img_name)
                # Remplacer le chemin dans le Markdown du ZIP pour cibler images/base_img_name
                archive_markdown = archive_markdown.replace(img_path, f"images/{base_img_name}")

        # Écrire le fichier Markdown à la racine du ZIP
        zf.writestr(md_filename, archive_markdown.encode("utf-8"))

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"'
        }
    )

@router.get("/{document_id}/download-pack")
async def download_existing_document_pack(
    document_id: str,
    filename: Optional[str] = Query(None, description="Nom ou fragment de nom de fichier")
):
    """
    Génère et télécharge le pack ZIP (.md + dossier images/) pour un document déjà indexé dans une collection.
    """
    doc_res = await get_document_markdown(document_id=document_id, filename=filename)
    md_content = doc_res["markdown_content"]
    real_filename = doc_res["filename"]

    req = DownloadPackRequest(filename=real_filename, markdown_content=md_content)
    return await download_document_pack(req)

@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """
    Supprime un document d'une collection dans Albert API et nettoie les métadonnées locales.
    """
    await albert_client.delete_document(document_id)

    docs = get_local_docs()
    target_doc = None
    remaining_docs = []
    for d in docs:
        if str(d.get("id")) == str(document_id) or str(d.get("name")) == str(document_id) or str(d.get("filename")) == str(document_id):
            target_doc = d
        else:
            remaining_docs.append(d)
    save_local_docs(remaining_docs)

    if target_doc and target_doc.get("collection_id"):
        target_col_id = str(target_doc.get("collection_id"))
        cols = get_local_collections()
        for col in cols:
            if str(col.get("id")) == target_col_id or str(col.get("name")) == target_col_id:
                curr_count = col.get("document_count", col.get("documents", 1))
                col["document_count"] = max(0, curr_count - 1)
        save_local_collections(cols)

    if target_doc:
        import shutil
        col_folder = DocumentConverter._sanitize_path_segment(target_doc.get("collection_id") or "default")
        doc_folder = DocumentConverter._sanitize_path_segment(os.path.splitext(target_doc.get("filename", ""))[0])
        doc_img_dir = os.path.join(settings.IMAGE_STORAGE_DIR, col_folder, doc_folder)
        if os.path.exists(doc_img_dir):
            try:
                shutil.rmtree(doc_img_dir)
            except Exception:
                pass

    return {
        "status": "success",
        "message": f"Document {document_id} supprimé avec succès",
        "deleted_id": document_id
    }

import os
import pymupdf as fitz
import pymupdf4llm
import docx
import openpyxl
from bs4 import BeautifulSoup
import datetime

class DocumentConverter:
    """
    Service de conversion universelle de documents (PDF, DOCX, XLSX, TXT, HTML)
    vers le format Markdown (.md) préservant l'ordre séquentiel exact des paragraphes et des tableaux.
    """

    @staticmethod
    def convert_to_markdown(file_path: str, filename: str, collection_name: str = "") -> dict:
        ext = os.path.splitext(filename)[1].lower()
        
        raw_text = ""
        tables_count = 0
        pages_count = 1

        if ext == ".pdf":
            raw_text, pages_count, tables_count = DocumentConverter._convert_pdf(file_path)
        elif ext in [".docx", ".doc"]:
            raw_text, tables_count = DocumentConverter._convert_docx(file_path)
        elif ext in [".xlsx", ".xls"]:
            raw_text, tables_count = DocumentConverter._convert_xlsx(file_path)
        elif ext in [".html", ".htm"]:
            raw_text = DocumentConverter._convert_html(file_path)
        elif ext in [".txt", ".md"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_text = f.read()
        else:
            raise ValueError(f"Format non supporté: {ext}")

        cleaned_text = DocumentConverter._clean_markdown_text(raw_text)

        metadata_header = (
            "---\n"
            f"title: \"{filename}\"\n"
            f"converted_at: \"{datetime.datetime.now().isoformat()}\"\n"
            f"source_format: \"{ext}\"\n"
            f"pages: {pages_count}\n"
            f"tables_detected: {tables_count}\n"
            f"target_collection: \"{collection_name}\"\n"
            "---\n\n"
        )

        final_markdown = metadata_header + cleaned_text

        return {
            "markdown_content": final_markdown,
            "filename": filename,
            "pages_count": pages_count,
            "tables_count": tables_count,
            "char_count": len(final_markdown)
        }

    @staticmethod
    def _convert_pdf(file_path: str) -> tuple[str, int, int]:
        """
        Conversion PDF haute fidélité respectant l'ordre de lecture et l'emplacement exact des tableaux via pymupdf4llm.
        """
        doc = fitz.open(file_path)
        pages_count = len(doc)
        tables_count = 0

        for page in doc:
            tabs = page.find_tables()
            if tabs and tabs.tables:
                tables_count += len(tabs.tables)

        try:
            md_text = pymupdf4llm.to_markdown(file_path, show_progress=False)
            return md_text, pages_count, tables_count
        except Exception as e:
            print(f"[DocumentConverter] Fallback PyMuPDF standard: {e}")
            md_content = []
            for page_num in range(pages_count):
                page = doc[page_num]
                md_content.append(f"\n\n## Page {page_num + 1}\n\n")
                
                blocks = page.get_text("blocks")
                blocks.sort(key=lambda b: (b[1], b[0]))
                
                for b in blocks:
                    block_text = b[4].strip()
                    if block_text:
                        md_content.append(f"{block_text}\n\n")

            return "".join(md_content), pages_count, tables_count

    @staticmethod
    def _convert_docx(file_path: str) -> tuple[str, int]:
        """
        Conversion Word (.docx) respectant l'ordre séquentiel exact entre paragraphes et tableaux.
        """
        doc = docx.Document(file_path)
        md_content = []
        tables_count = len(doc.tables)

        for element in doc.element.body:
            if element.tag.endswith('p'):
                p = docx.text.paragraph.Paragraph(element, doc)
                text = p.text.strip()
                if not text:
                    continue
                style_name = p.style.name if p.style else ""
                if style_name.startswith("Heading 1") or style_name.startswith("Titre 1"):
                    md_content.append(f"\n# {text}\n")
                elif style_name.startswith("Heading 2") or style_name.startswith("Titre 2"):
                    md_content.append(f"\n## {text}\n")
                elif style_name.startswith("Heading 3") or style_name.startswith("Titre 3"):
                    md_content.append(f"\n### {text}\n")
                else:
                    md_content.append(f"\n{text}\n")

            elif element.tag.endswith('tbl'):
                table = docx.table.Table(element, doc)
                md_content.append("\n\n")
                rows = []
                for row in table.rows:
                    cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    rows.append(cells)
                if rows and len(rows) > 0:
                    header = rows[0]
                    md_content.append("| " + " | ".join(header) + " |\n")
                    md_content.append("| " + " | ".join(["---"] * len(header)) + " |\n")
                    for r in rows[1:]:
                        md_content.append("| " + " | ".join(r) + " |\n")
                md_content.append("\n\n")

        return "".join(md_content), tables_count

    @staticmethod
    def _convert_xlsx(file_path: str) -> tuple[str, int]:
        """
        Conversion Excel (.xlsx) sous forme de tableaux Markdown structurés par feuille.
        """
        wb = openpyxl.load_workbook(file_path, data_only=True)
        md_content = []
        tables_count = len(wb.sheetnames)

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            md_content.append(f"\n# Feuille: {sheet_name}\n\n")
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                continue
            
            clean_rows = []
            for row in rows:
                if any(cell is not None for cell in row):
                    clean_rows.append([str(c or "").strip().replace("\n", " ") for c in row])

            if clean_rows:
                header = clean_rows[0]
                md_content.append("| " + " | ".join(header) + " |\n")
                md_content.append("| " + " | ".join(["---"] * len(header)) + " |\n")
                for r in clean_rows[1:]:
                    md_content.append("| " + " | ".join(r) + " |\n")
            md_content.append("\n\n")

        return "".join(md_content), tables_count

    @staticmethod
    def _convert_html(file_path: str) -> str:
        """
        Conversion HTML conservant la structure des titres et des tableaux dans leur ordre d'apparition.
        """
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        
        for element in soup(["script", "style", "nav", "footer"]):
            element.decompose()

        md_content = []
        for elem in soup.find_all(["h1", "h2", "h3", "h4", "p", "table"]):
            if elem.name in ["h1", "h2", "h3", "h4"]:
                level = int(elem.name[1])
                md_content.append(f"\n{'#' * level} {elem.get_text().strip()}\n")
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
                    md_content.append("\n\n| " + " | ".join(rows[0]) + " |\n")
                    md_content.append("| " + " | ".join(["---"] * len(rows[0])) + " |\n")
                    for r in rows[1:]:
                        md_content.append("| " + " | ".join(r) + " |\n")
                    md_content.append("\n\n")

        return "".join(md_content)

    @staticmethod
    def _clean_markdown_text(text: str) -> str:
        """Nettoie les sauts de ligne multiples consécutifs."""
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

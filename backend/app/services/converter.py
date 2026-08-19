import os
import pymupdf as fitz
import docx
import openpyxl
from bs4 import BeautifulSoup
import datetime

class DocumentConverter:
    """
    Service de conversion universelle de documents (PDF, DOCX, XLSX, TXT, HTML)
    vers le format Markdown (.md) enrichi de métadonnées YAML Front-Matter.
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

        # Build YAML Front-Matter Metadata
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

        final_markdown = metadata_header + raw_text

        return {
            "markdown_content": final_markdown,
            "filename": filename,
            "pages_count": pages_count,
            "tables_count": tables_count,
            "char_count": len(final_markdown)
        }

    @staticmethod
    def _convert_pdf(file_path: str) -> tuple[str, int, int]:
        doc = fitz.open(file_path)
        pages_count = len(doc)
        md_content = []
        tables_count = 0

        for page_num in range(pages_count):
            page = doc[page_num]
            md_content.append(f"\n\n## Page {page_num + 1}\n\n")
            
            # Extract tables if present
            tabs = page.find_tables()
            if tabs.tables:
                tables_count += len(tabs.tables)
                for table in tabs.tables:
                    df_rows = table.extract()
                    if df_rows and len(df_rows) > 0:
                        # Header row
                        header = df_rows[0]
                        clean_header = [str(c or "").strip().replace("\n", " ") for c in header]
                        md_content.append("| " + " | ".join(clean_header) + " |")
                        md_content.append("| " + " | ".join(["---"] * len(clean_header)) + " |")
                        # Data rows
                        for row in df_rows[1:]:
                            clean_row = [str(c or "").strip().replace("\n", " ") for c in row]
                            md_content.append("| " + " | ".join(clean_row) + " |")
                        md_content.append("\n")
            else:
                # Standard text extraction
                text = page.get_text("text")
                md_content.append(text)

        return "".join(md_content), pages_count, tables_count

    @staticmethod
    def _convert_docx(file_path: str) -> tuple[str, int]:
        doc = docx.Document(file_path)
        md_content = []
        tables_count = len(doc.tables)

        for element in doc.paragraphs:
            text = element.text.strip()
            if not text:
                continue
            if element.style.name.startswith("Heading 1"):
                md_content.append(f"\n# {text}\n")
            elif element.style.name.startswith("Heading 2"):
                md_content.append(f"\n## {text}\n")
            elif element.style.name.startswith("Heading 3"):
                md_content.append(f"\n### {text}\n")
            else:
                md_content.append(f"{text}\n")

        for table in doc.tables:
            md_content.append("\n\n")
            rows = []
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                rows.append(cells)
            if rows:
                md_content.append("| " + " | ".join(rows[0]) + " |")
                md_content.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
                for r in rows[1:]:
                    md_content.append("| " + " | ".join(r) + " |")
            md_content.append("\n")

        return "\n".join(md_content), tables_count

    @staticmethod
    def _convert_xlsx(file_path: str) -> tuple[str, int]:
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
                md_content.append("| " + " | ".join(header) + " |")
                md_content.append("| " + " | ".join(["---"] * len(header)) + " |")
                for r in clean_rows[1:]:
                    md_content.append("| " + " | ".join(r) + " |")
            md_content.append("\n")

        return "".join(md_content), tables_count

    @staticmethod
    def _convert_html(file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        
        # Remove script and style elements
        for element in soup(["script", "style", "nav", "footer"]):
            element.decompose()

        return soup.get_text(separator="\n\n")

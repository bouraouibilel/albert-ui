from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
import uuid
import json
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

@router.post("/convert")
async def convert_document(
    file: UploadFile = File(...),
    collection_name: Optional[str] = Form("")
):
    """
    Étape cruciale 1: Reçoit un document brut, le convertit vers le format Markdown (.md)
    et le retourne pour prévisualisation et édition dans l'interface d'administration.
    """
    try:
        # Save uploaded raw file
        temp_id = str(uuid.uuid4())[:8]
        raw_filename = f"{temp_id}_{file.filename}"
        raw_file_path = os.path.join(settings.UPLOAD_DIR, raw_filename)
        
        content = await file.read()
        with open(raw_file_path, "wb") as f:
            f.write(content)

        # Execute conversion to Markdown
        conversion_result = DocumentConverter.convert_to_markdown(
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
    Étape cruciale 2: Prend le contenu Markdown validé/édité et l'envoie dans la collection Albert API.
    """
    temp_id = str(uuid.uuid4())[:8]
    md_filename = payload.filename if payload.filename.endswith(".md") else f"{payload.filename}.md"
    file_path = os.path.join(settings.CONVERTED_DIR, f"{temp_id}_{md_filename}")

    # Write approved Markdown to file
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(payload.markdown_content)

    # Push to Albert API
    result = await albert_client.upload_document(
        collection_id=payload.collection_id,
        file_path=file_path,
        filename=md_filename
    )

    # Track in local docs meta
    docs = get_local_docs()
    new_doc_meta = {
        "id": result.get("id", temp_id),
        "filename": md_filename,
        "collection_id": payload.collection_id,
        "original_format": payload.original_format,
        "size_chars": len(payload.markdown_content),
        "status": "indexed_in_albert",
        "ingested_at": result.get("created_at")
    }
    docs.append(new_doc_meta)
    save_local_docs(docs)

    # Update collection doc count
    cols = get_local_collections()
    for col in cols:
        if col.get("id") == payload.collection_id or col.get("name") == payload.collection_id:
            col["document_count"] = col.get("document_count", 0) + 1
    save_local_collections(cols)

    return {
        "status": "success",
        "message": "Document Markdown indexé avec succès dans Albert API",
        "document_metadata": new_doc_meta
    }

@router.get("/")
async def list_documents(collection_id: Optional[str] = None):
    docs = get_local_docs()
    if collection_id:
        docs = [d for d in docs if d.get("collection_id") == collection_id]
    return docs

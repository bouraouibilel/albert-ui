from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
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
    Étape cruciale 2: Envoie le document Markdown validé/édité vers la collection Albert API.
    """
    temp_id = str(uuid.uuid4())[:8]
    md_filename = payload.filename if payload.filename.endswith(".md") else f"{payload.filename}.md"
    file_path = os.path.join(settings.CONVERTED_DIR, f"{temp_id}_{md_filename}")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(payload.markdown_content)

    result = await albert_client.upload_document(
        collection_id=payload.collection_id,
        file_path=file_path,
        filename=md_filename
    )

    docs = get_local_docs()
    new_doc_meta = {
        "id": result.get("id", temp_id),
        "filename": md_filename,
        "name": payload.filename,
        "collection_id": payload.collection_id,
        "original_format": payload.original_format,
        "size_chars": len(payload.markdown_content),
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
        "message": "Document Markdown indexé avec succès dans Albert API",
        "document_metadata": new_doc_meta
    }

@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """
    Supprime un document d'une collection dans Albert API et nettoie les métadonnées locales.
    """
    # 1. Appel Albert API pour supprimer le document distant
    await albert_client.delete_document(document_id)

    # 2. Suppression dans les métadonnées locales documents_meta.json
    docs = get_local_docs()
    target_doc = None
    remaining_docs = []
    for d in docs:
        if str(d.get("id")) == str(document_id) or str(d.get("name")) == str(document_id) or str(d.get("filename")) == str(document_id):
            target_doc = d
        else:
            remaining_docs.append(d)
    save_local_docs(remaining_docs)

    # 3. Décrémentation du compteur de documents dans les collections locales
    if target_doc and target_doc.get("collection_id"):
        target_col_id = str(target_doc.get("collection_id"))
        cols = get_local_collections()
        for col in cols:
            if str(col.get("id")) == target_col_id or str(col.get("name")) == target_col_id:
                curr_count = col.get("document_count", col.get("documents", 1))
                col["document_count"] = max(0, curr_count - 1)
        save_local_collections(cols)

    return {
        "status": "success",
        "message": f"Document {document_id} supprimé avec succès",
        "deleted_id": document_id
    }


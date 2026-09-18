from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List
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

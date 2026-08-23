import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from app.core.config import settings
from app.services.watcher_service import watcher_service

router = APIRouter(prefix="/watchers", tags=["watchers"])

class WatcherCreateRequest(BaseModel):
    collection_id: str = Field(..., description="ID de la collection Albert cible")
    collection_name: Optional[str] = Field("", description="Nom lisible de la collection")
    folder_path: str = Field(..., description="Chemin physique absolu du répertoire à écouter")
    filter_pattern: Optional[str] = Field("*", description="Filtre sur le nom de fichier (ex: '*PASRAU*.docx', '*.pdf')")
    enabled: Optional[bool] = Field(True, description="Si l'écoute est activée immédiatement")
    recursive: Optional[bool] = Field(False, description="Parcourir récursivement les sous-dossiers")
    watcher_id: Optional[str] = Field(None, description="ID si mise à jour")

@router.get("", response_model=Dict[str, Any])
async def list_watchers():
    """
    Récupère la liste des dossiers d'écoute configurés ainsi que les statistiques globales.
    """
    watchers = watcher_service.get_watchers()
    history = watcher_service.get_history(limit=500)

    total_watchers = len(watchers)
    active_watchers = sum(1 for w in watchers if w.get("enabled", True))
    
    total_detected = len(history)
    processing_count = sum(1 for h in history if h.get("status") == "processing")
    completed_count = sum(1 for h in history if h.get("status") == "completed")
    error_count = sum(1 for h in history if h.get("status") == "error")

    return {
        "watchers": watchers,
        "stats": {
            "total_watchers": total_watchers,
            "active_watchers": active_watchers,
            "total_detected": total_detected,
            "processing_count": processing_count,
            "completed_count": completed_count,
            "error_count": error_count
        }
    }

@router.get("/live", response_model=Dict[str, Any])
async def get_live_status(
    status: Optional[str] = Query(None, description="Filtrer l'historique par statut"),
    limit: int = Query(200, ge=1, le=500)
):
    """
    Endpoint haute performance fournissant l'état complet en temps réel (stats + watchers + historique) en un seul appel.
    """
    watchers = watcher_service.get_watchers()
    all_history = watcher_service.get_history(limit=500)

    total_watchers = len(watchers)
    active_watchers = sum(1 for w in watchers if w.get("enabled", True))
    
    total_detected = len(all_history)
    processing_count = sum(1 for h in all_history if h.get("status") == "processing")
    completed_count = sum(1 for h in all_history if h.get("status") == "completed")
    error_count = sum(1 for h in all_history if h.get("status") == "error")

    # Filtrage de la portion d'historique renvoyée
    filtered_history = all_history
    if status and status.lower() != "all":
        filtered_history = [i for i in all_history if i.get("status") == status.lower()]
    filtered_history = filtered_history[:limit]

    return {
        "watchers": watchers,
        "stats": {
            "total_watchers": total_watchers,
            "active_watchers": active_watchers,
            "total_detected": total_detected,
            "processing_count": processing_count,
            "completed_count": completed_count,
            "error_count": error_count
        },
        "history": filtered_history
    }

@router.post("", response_model=Dict[str, Any])
async def create_or_update_watcher(payload: WatcherCreateRequest):
    """
    Configure ou met à jour l'écoute d'un répertoire pour une collection Albert.
    """
    try:
        watcher = watcher_service.create_or_update_watcher(
            collection_id=payload.collection_id,
            collection_name=payload.collection_name,
            folder_path=payload.folder_path,
            filter_pattern=payload.filter_pattern,
            enabled=payload.enabled,
            recursive=payload.recursive,
            watcher_id=payload.watcher_id
        )
        return {
            "status": "success",
            "message": f"Dossier d'écoute pour la collection '{watcher.get('collection_name')}' configuré avec succès",
            "watcher": watcher
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{watcher_id}")
async def delete_watcher(watcher_id: str):
    """
    Supprime la configuration d'écoute d'un répertoire.
    """
    success = watcher_service.delete_watcher(watcher_id)
    if not success:
        raise HTTPException(status_code=404, detail="Dossier d'écoute introuvable")
    return {"status": "success", "message": f"Dossier d'écoute #{watcher_id} supprimé"}

@router.post("/{watcher_id}/toggle")
async def toggle_watcher(watcher_id: str):
    """
    Active ou met en pause l'écoute automatique d'un répertoire.
    """
    updated = watcher_service.toggle_watcher(watcher_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Dossier d'écoute introuvable")
    status_str = "activée" if updated.get("enabled") else "mise en pause"
    return {
        "status": "success",
        "message": f"Écoute du répertoire {status_str}",
        "watcher": updated
    }

@router.get("/history", response_model=List[Dict[str, Any]])
async def get_history(
    status: Optional[str] = Query(None, description="Filtrer par statut: processing, completed, error"),
    collection_id: Optional[str] = Query(None, description="Filtrer par collection"),
    limit: int = Query(200, ge=1, le=500)
):
    """
    Récupère le journal et l'état de suivi en temps réel de tous les fichiers détectés.
    """
    return watcher_service.get_history(status=status, collection_id=collection_id, limit=limit)

@router.post("/scan-now")
async def scan_folders_now():
    """
    Déclenche manuellement et immédiatement un scan de tous les dossiers d'écoute actifs.
    """
    processed = await watcher_service.scan_all_folders()
    return {
        "status": "success",
        "message": f"Scan terminé. {processed} nouveau(x) document(s) mis en traitement.",
        "new_documents_count": processed
    }

@router.post("/history/{item_id}/retry")
async def retry_file(item_id: str):
    """
    Relance le traitement d'un document qui a échoué.
    """
    ok = await watcher_service.retry_history_item(item_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Impossible de relancer le document (fichier source introuvable ou inexistant).")
    return {"status": "success", "message": "Traitement du document relancé avec succès"}

@router.get("/history/{item_id}/markdown", response_model=Dict[str, Any])
async def get_watcher_history_markdown(item_id: str):
    """
    Récupère le contenu du document Markdown (.md) converti issu du watcher.
    """
    history = watcher_service.get_history(limit=500)
    item = next((h for h in history if h.get("id") == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Élément d'historique introuvable")
    
    md_filename = item.get("markdown_file")
    converted_dir = settings.CONVERTED_DIR
    target_path = None

    if md_filename:
        p = os.path.join(converted_dir, md_filename)
        if os.path.exists(p):
            target_path = p

    if not target_path and os.path.exists(converted_dir):
        stem = Path(item.get("filename", "")).stem
        for fname in os.listdir(converted_dir):
            if fname.endswith(f"_{stem}.md") or fname == f"{stem}.md" or (stem and len(stem) > 3 and stem in fname and fname.endswith(".md")):
                target_path = os.path.join(converted_dir, fname)
                md_filename = fname
                break

    if not target_path or not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"Fichier Markdown introuvable pour '{item.get('filename')}'.")

    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()

    stat = os.stat(target_path)
    return {
        "status": "success",
        "item_id": item_id,
        "filename": item.get("filename"),
        "markdown_file": md_filename or os.path.basename(target_path),
        "collection_id": item.get("collection_id"),
        "collection_name": item.get("collection_name"),
        "markdown_content": content,
        "char_count": len(content),
        "file_size": stat.st_size,
        "mtime": stat.st_mtime
    }

@router.delete("/history/{item_id}")
async def delete_history_item(item_id: str):
    """
    Supprime une ligne d'historique de suivi.
    """
    ok = watcher_service.delete_history_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Élément d'historique introuvable")
    return {"status": "success", "message": "Élément d'historique supprimé"}

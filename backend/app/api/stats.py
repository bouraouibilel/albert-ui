import os
from pathlib import Path
from fastapi import APIRouter
from typing import Dict, Any, List

from app.core.config import settings
from app.services.albert_client import albert_client
from app.services.watcher_service import watcher_service

try:
    from app.core.logger import get_recent_logs, get_user_usage_stats
except ImportError:
    def get_recent_logs():
        return []
    def get_user_usage_stats():
        return []

router = APIRouter(prefix="/stats", tags=["stats"])

@router.get("", response_model=Dict[str, Any])
@router.get("/", response_model=Dict[str, Any])
@router.get("/overview", response_model=Dict[str, Any])
@router.get("/overview/", response_model=Dict[str, Any])
async def get_statistics_overview():
    """
    Récupère une synthèse complète des statistiques de l'API Albert et des services locaux :
    - Collections Albert API (totaux, visibilité)
    - Documents indexés (formats, volumes, chunks)
    - Dossiers d'écoute et ingesteurs locaux (watchers, conversion Markdown)
    - Utilisation des modèles LLM (Vision 24B, Reranker, logs)
    - Consommation de requêtes par utilisateur / IP client
    """
    # 1. Collections Albert API
    collections = await albert_client.list_collections(limit=200)
    total_collections = len(collections)
    public_collections = sum(1 for c in collections if c.get("visibility") == "public")
    private_collections = total_collections - public_collections

    # 2. Documents Albert API
    documents = await albert_client.list_documents(limit=500)
    total_documents = len(documents)

    format_distribution: Dict[str, int] = {}
    total_doc_size_bytes = 0
    total_chunks_count = 0

    per_collection_doc_counts: Dict[str, int] = {}

    for doc in documents:
        # Format distribution
        fname = doc.get("name", "") or doc.get("filename", "") or doc.get("title", "")
        ext = Path(fname).suffix.lower() if Path(fname).suffix else ""
        if not ext:
            ext = ".md" if "markdown" in fname.lower() else ".pdf"
        format_distribution[ext] = format_distribution.get(ext, 0) + 1

        # Size & Chunks
        size = doc.get("size") or doc.get("file_size") or doc.get("char_count") or 0
        total_doc_size_bytes += size

        chunks = doc.get("chunks") or doc.get("chunk_count") or doc.get("nb_chunks") or 0
        total_chunks_count += chunks

        # Collection mapping
        col_id = str(doc.get("collection_id") or doc.get("collection") or "non_assigne")
        per_collection_doc_counts[col_id] = per_collection_doc_counts.get(col_id, 0) + 1

    # 3. Fichiers Markdown Convertis Locaux
    converted_dir = settings.CONVERTED_DIR
    converted_files_count = 0
    total_converted_size_bytes = 0
    if os.path.exists(converted_dir):
        for fname in os.listdir(converted_dir):
            if fname.endswith(".md"):
                converted_files_count += 1
                try:
                    fpath = os.path.join(converted_dir, fname)
                    total_converted_size_bytes += os.path.getsize(fpath)
                except Exception:
                    pass

    # 4. Watcher Service Stats
    watchers = watcher_service.get_watchers()
    history = watcher_service.get_history(limit=500)
    watcher_stats = {
        "total_watchers": len(watchers),
        "active_watchers": sum(1 for w in watchers if w.get("enabled", True)),
        "total_detected": len(history),
        "processing_count": sum(1 for h in history if h.get("status") == "processing"),
        "completed_count": sum(1 for h in history if h.get("status") == "completed"),
        "error_count": sum(1 for h in history if h.get("status") == "error")
    }

    # 5. Logs & Event Metrics
    logs = get_recent_logs()
    vision_calls = sum(1 for l in logs if l.get("category") == "LLM-VISION")
    rerank_calls = sum(1 for l in logs if l.get("category") == "LLM-RERANK")
    albert_api_calls = sum(1 for l in logs if l.get("category") == "ALBERT-API")
    error_logs = sum(1 for l in logs if l.get("level") == "ERROR")

    # 6. User Request Usage Breakdown
    users_usage = get_user_usage_stats()

    return {
        "status": "online",
        "api_configured": bool(settings.ALBERT_API_KEY),
        "base_url": settings.ALBERT_API_BASE_URL,
        "collections": {
            "total": total_collections,
            "public": public_collections,
            "private": private_collections,
            "per_collection_docs": per_collection_doc_counts
        },
        "documents": {
            "total": total_documents,
            "format_distribution": format_distribution,
            "total_size_bytes": total_doc_size_bytes,
            "total_size_mb": round(total_doc_size_bytes / (1024 * 1024), 2),
            "estimated_chunks": total_chunks_count
        },
        "local_storage": {
            "converted_md_files": converted_files_count,
            "converted_size_bytes": total_converted_size_bytes,
            "converted_size_mb": round(total_converted_size_bytes / (1024 * 1024), 2)
        },
        "watchers": watcher_stats,
        "activity_metrics": {
            "total_logs": len(logs),
            "vision_24b_calls": vision_calls,
            "reranker_calls": rerank_calls,
            "albert_api_requests": albert_api_calls,
            "errors_count": error_logs
        },
        "users_usage": users_usage
    }

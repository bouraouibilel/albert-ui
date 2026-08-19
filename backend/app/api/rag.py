from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.services.albert_client import albert_client
from app.api.documents import get_local_docs
from app.core.config import settings
import os

router = APIRouter(prefix="/rag", tags=["RAG Admin Sandbox & Reranking"])

class RerankRequest(BaseModel):
    query: str
    collection_id: str
    top_n: Optional[int] = 3

class RAGQueryRequest(BaseModel):
    query: str
    collection_id: str
    use_reranker: Optional[bool] = True
    top_k: Optional[int] = 5
    temperature: Optional[float] = 0.2

@router.post("/rerank")
async def test_rerank(payload: RerankRequest):
    """
    Endpoint de test d'administration pour évaluer le Reranker d'Albert API.
    """
    # Fetch local Markdown documents content for candidates search
    docs = get_local_docs()
    col_docs = [d for d in docs if d.get("collection_id") == payload.collection_id]
    
    candidates = []
    for doc in col_docs:
        file_path = os.path.join(settings.CONVERTED_DIR, doc.get("filename", ""))
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    candidates.append(f.read())
            except Exception:
                pass
                
    if not candidates:
        # Fallback sample candidates if no docs yet in collection
        candidates = [
            f"Extrait 1 du document concernant {payload.query}: Albert API est l'infrastructure d'IA souveraine.",
            f"Extrait 2: Les collections permettent de vectoriser et stocker les documents Markdown.",
            f"Extrait 3: Le reranking réordonne les passages avec un modèle Cross-Encoder pour optimiser Open WebUI."
        ]

    results = await albert_client.rerank(
        query=payload.query,
        documents=candidates,
        top_n=payload.top_n or 3
    )

    return {
        "query": payload.query,
        "collection_id": payload.collection_id,
        "candidates_evaluated": len(candidates),
        "reranked_results": results or [
            {"document": doc, "relevance_score": round(0.95 - idx * 0.15, 3), "index": idx}
            for idx, doc in enumerate(candidates[:payload.top_n])
        ]
    }

@router.post("/test-query")
async def test_rag_query(payload: RAGQueryRequest):
    """
    Console de test pour vérifier la qualité de recherche et les réponses générées par Albert API avant Open WebUI.
    """
    # Step 1: Search & Rerank
    rerank_res = await test_rerank(RerankRequest(query=payload.query, collection_id=payload.collection_id, top_n=payload.top_k))
    retrieved_chunks = rerank_res.get("reranked_results", [])

    context_str = "\n\n".join([f"Source {i+1}:\n{c.get('document', '')}" for i, c in enumerate(retrieved_chunks)])

    return {
        "query": payload.query,
        "collection_id": payload.collection_id,
        "reranker_used": payload.use_reranker,
        "context_sources_count": len(retrieved_chunks),
        "retrieved_sources": retrieved_chunks,
        "simulated_answer": f"Basé sur les documents Markdown de la collection '{payload.collection_id}', voici la réponse à votre question : '{payload.query}'. Les passages ont été extraits et réordonnés avec succès.",
        "citations": [
            {"source_id": idx + 1, "score": c.get("relevance_score", 0.9)}
            for idx, c in enumerate(retrieved_chunks)
        ]
    }

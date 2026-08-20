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
    Endpoint de test d'administration pour réordonner les véritables chunks d'une collection Albert API.
    """
    candidates = []

    # 1. Tenter de récupérer les vrais chunks réels de la collection depuis Albert API
    remote_docs = await albert_client.list_documents(collection_id=payload.collection_id, limit=20)
    for doc in remote_docs:
        doc_id = doc.get("id")
        if doc_id:
            chunks = await albert_client.get_document_chunks(str(doc_id), limit=30)
            for c in chunks:
                text_content = c.get("content", "").strip()
                if text_content:
                    candidates.append(text_content)

    # 2. Si aucun chunk distant, chercher dans les fichiers locaux
    if not candidates:
        docs = get_local_docs()
        col_docs = [d for d in docs if str(d.get("collection_id")) == str(payload.collection_id)]
        for doc in col_docs:
            file_path = os.path.join(settings.CONVERTED_DIR, doc.get("filename", ""))
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        # découpe simple par paragraphe pour fallback
                        paras = [p.strip() for p in content.split("\n\n") if p.strip()]
                        candidates.extend(paras[:20])
                except Exception:
                    pass

    # 3. Exécuter le réordonnancement avec le modèle Cross-Encoder 'bge-reranker-v2-m3'
    reranked_results = await albert_client.rerank(
        query=payload.query,
        documents=candidates,
        top_n=payload.top_n or 3,
        model="bge-reranker-v2-m3"
    )

    return {
        "query": payload.query,
        "collection_id": payload.collection_id,
        "candidates_evaluated": len(candidates),
        "reranked_results": reranked_results
    }

@router.post("/test-query")
async def test_rag_query(payload: RAGQueryRequest):
    """
    Console de test RAG complète pour évaluer la pertinence et les sources extraites.
    """
    rerank_res = await test_rerank(RerankRequest(query=payload.query, collection_id=payload.collection_id, top_n=payload.top_k))
    retrieved_chunks = rerank_res.get("reranked_results", [])

    return {
        "query": payload.query,
        "collection_id": payload.collection_id,
        "reranker_used": payload.use_reranker,
        "candidates_evaluated": rerank_res.get("candidates_evaluated", 0),
        "context_sources_count": len(retrieved_chunks),
        "retrieved_sources": retrieved_chunks,
        "simulated_answer": f"Basé sur la collection '{payload.collection_id}', voici les extraits les plus pertinents identifiés par le reranker 'bge-reranker-v2-m3'.",
        "citations": [
            {"source_id": idx + 1, "score": c.get("relevance_score", 0.0)}
            for idx, c in enumerate(retrieved_chunks)
        ]
    }

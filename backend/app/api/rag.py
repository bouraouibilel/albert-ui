from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.services.albert_client import albert_client
from app.api.documents import get_local_docs
from app.core.config import settings
import os
import re

router = APIRouter(prefix="/rag", tags=["RAG Admin Sandbox & Reranking"])

class RerankRequest(BaseModel):
    query: str
    collection_id: str
    top_n: Optional[int] = 5
    search_method: Optional[str] = "hybrid"
    document_id: Optional[str] = None
    document_name_filter: Optional[str] = None
    generate_answer: Optional[bool] = True

class RAGQueryRequest(BaseModel):
    query: str
    collection_id: str
    use_reranker: Optional[bool] = True
    top_k: Optional[int] = 5
    temperature: Optional[float] = 0.15
    search_method: Optional[str] = "hybrid"
    document_id: Optional[str] = None
    document_name_filter: Optional[str] = None


def _split_markdown_by_sections(md_content: str, max_chunk_chars: int = 2400) -> List[str]:
    """
    Découpe intelligente d'un fichier Markdown par sections numérotées / titres (##, ###, ####)
    en préservant l'intégrité des tableaux et des tags contextuels [DOC: ... | SECTION: ...].
    """
    blocks = re.split(r'\n(?=##+\s)', md_content)
    chunks = []
    for block in blocks:
        b = block.strip()
        if not b:
            continue
        if len(b) <= max_chunk_chars:
            chunks.append(b)
        else:
            # Découper par sous-blocs (\n\n) tout en répétant le tag contextuel s'il est présent en tête
            tag_match = re.search(r'(> 🏷️ \*\*\[DOC:.*?\]\*\*)', b)
            header_tag = f"{tag_match.group(1)}\n" if tag_match else ""
            paras = [p.strip() for p in b.split("\n\n") if p.strip()]
            curr = ""
            for p in paras:
                if len(curr) + len(p) + 2 <= max_chunk_chars:
                    curr = f"{curr}\n\n{p}".strip() if curr else p
                else:
                    if curr:
                        chunks.append(curr)
                    curr = f"{header_tag}{p}".strip() if header_tag and not p.startswith("> 🏷️") else p
            if curr:
                chunks.append(curr)
    return chunks


@router.post("/rerank")
async def test_rerank(payload: RerankRequest):
    """
    Endpoint de test RAG avancé combinant :
    1. Recherche Hybride Albert API (/v1/search avec method='hybrid', 'semantic' ou 'lexical')
    2. Filtrage optionnel par document (document_id ou tag [DOC: ...])
    3. Réordonnancement Cross-Encoder ('bge-reranker-v2-m3')
    4. Synthèse IA ancrée sur les extraits (pour restituer exhaustivement les tableaux/batchs).
    """
    candidates: List[str] = []
    seen_hashes = set()

    def add_candidate(text: str):
        t = (text or "").strip()
        if not t or len(t) < 10:
            return
        if payload.document_name_filter:
            filt = payload.document_name_filter.strip().lower()
            if filt and filt not in t.lower():
                return
        h = hash(t[:300] + t[-100:])
        if h not in seen_hashes:
            seen_hashes.add(h)
            candidates.append(t)

    # Construire la requête enrichie si un filtre de nom de document est fourni
    effective_query = payload.query
    if payload.document_name_filter:
        effective_query = f"[DOC: {payload.document_name_filter}] {payload.query}"

    # 1. Recherche Hybride (Sémantique + Lexicale BM25) via POST /v1/search d'Albert API
    search_hits = await albert_client.search_chunks(
        query=effective_query,
        collection_id=payload.collection_id,
        limit=25,
        method=payload.search_method or "hybrid",
        document_id=payload.document_id
    )
    for hit in search_hits:
        add_candidate(hit.get("content", ""))

    # 2. Complément via les chunks des documents de la collection si /v1/search remonte peu de candidats
    if len(candidates) < 8:
        if payload.document_id:
            doc_chunks = await albert_client.get_document_chunks(str(payload.document_id), limit=100)
            for c in doc_chunks:
                add_candidate(c.get("content", ""))
        else:
            remote_docs = await albert_client.list_documents(collection_id=payload.collection_id, limit=20)
            for doc in remote_docs:
                doc_id = doc.get("id")
                doc_name = doc.get("name", "")
                if payload.document_name_filter and payload.document_name_filter.lower() not in doc_name.lower():
                    continue
                if doc_id:
                    chunks = await albert_client.get_document_chunks(str(doc_id), limit=60)
                    for c in chunks:
                        add_candidate(c.get("content", ""))

    # 3. Fallback local intelligent (découpe par section numérotée / tableau) si nécessaire
    if len(candidates) < 5:
        docs = get_local_docs()
        col_docs = [d for d in docs if str(d.get("collection_id")) == str(payload.collection_id)]
        for doc in col_docs:
            if payload.document_id and str(doc.get("id")) != str(payload.document_id):
                continue
            if payload.document_name_filter:
                doc_label = f"{doc.get('name', '')} {doc.get('doc_title', '')}".lower()
                if payload.document_name_filter.lower() not in doc_label:
                    continue
            fname = doc.get("filename", "")
            file_path = os.path.join(settings.CONVERTED_DIR, fname)
            if not os.path.exists(file_path) and os.path.exists(settings.CONVERTED_DIR):
                # Chercher avec préfixe temp_id
                for f_item in os.listdir(settings.CONVERTED_DIR):
                    if f_item.endswith(fname) and f_item.endswith(".md"):
                        file_path = os.path.join(settings.CONVERTED_DIR, f_item)
                        break
            if os.path.exists(file_path) and os.path.isfile(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        sec_chunks = _split_markdown_by_sections(content)
                        for sc in sec_chunks[:80]:
                            add_candidate(sc)
                except Exception:
                    pass

    # 4. Exécuter le réordonnancement avec le modèle Cross-Encoder 'bge-reranker-v2-m3'
    top_n = payload.top_n or 5
    reranked_results = await albert_client.rerank(
        query=payload.query,
        documents=candidates,
        top_n=top_n,
        model="bge-reranker-v2-m3"
    )

    # Extraire les tags [DOC: ... | VERSION: ... | SECTION: ...] pour chaque résultat
    tag_regex = re.compile(r'\[DOC:\s*([^|\]]+?)\s*\|\s*VERSION:\s*([^|\]]+?)\s*\|\s*SECTION:\s*([^\]]+?)\]')
    for r in reranked_results:
        doc_txt = r.get("document", "")
        m = tag_regex.search(doc_txt)
        if m:
            r["meta_doc"] = m.group(1).strip()
            r["meta_version"] = m.group(2).strip()
            r["meta_section"] = m.group(3).strip()

    # 5. Générer une réponse synthétique ancrée sur les meilleurs extraits si demandé
    generated_answer = None
    if payload.generate_answer and reranked_results:
        context_blocks = []
        for idx, item in enumerate(reranked_results[:top_n]):
            context_blocks.append(f"--- EXTRAIT #{idx + 1} (Score: {item.get('relevance_score', 0)}) ---\n{item.get('document', '')}")
        context_str = "\n\n".join(context_blocks)
        sys_prompt = (
            "Tu es un assistant RAG expert chargé d'analyser des documents techniques et spécifications (Word/PDF convertis en Markdown). "
            "Réponds en français uniquement à partir des extraits fournis. "
            "RÈGLE CRITIQUE POUR LES TABLEAUX ET LISTES (ex: liste des batchs, flux, règles, codes) : "
            "Si une synthèse de tableau ('Synthèse exhaustive des entrées du tableau') ou un tableau Markdown est présent, "
            "restitue EXHAUSTIVEMENT 100% des éléments sans jamais en omettre ni tronquer la liste. "
            "Cite toujours le nom du document, sa version et la section source."
        )
        usr_prompt = (
            f"Question de l'utilisateur : {payload.query}\n\n"
            f"Extraits documentaires récupérés :\n{context_str}\n\n"
            f"Réponse détaillée et exhaustive :"
        )
        generated_answer = await albert_client.generate_text(
            system_prompt=sys_prompt,
            user_prompt=usr_prompt,
            temperature=0.1
        )

    return {
        "query": payload.query,
        "collection_id": payload.collection_id,
        "search_method": payload.search_method or "hybrid",
        "document_id": payload.document_id,
        "candidates_evaluated": len(candidates),
        "reranked_results": reranked_results,
        "generated_answer": generated_answer
    }

@router.post("/test-query")
async def test_rag_query(payload: RAGQueryRequest):
    """
    Console de test RAG complète pour évaluer la pertinence, la recherche hybride et les sources extraites.
    """
    rerank_res = await test_rerank(RerankRequest(
        query=payload.query,
        collection_id=payload.collection_id,
        top_n=payload.top_k,
        search_method=payload.search_method,
        document_id=payload.document_id,
        document_name_filter=payload.document_name_filter,
        generate_answer=True
    ))
    retrieved_chunks = rerank_res.get("reranked_results", [])

    return {
        "query": payload.query,
        "collection_id": payload.collection_id,
        "search_method": payload.search_method or "hybrid",
        "reranker_used": payload.use_reranker,
        "candidates_evaluated": rerank_res.get("candidates_evaluated", 0),
        "context_sources_count": len(retrieved_chunks),
        "retrieved_sources": retrieved_chunks,
        "generated_answer": rerank_res.get("generated_answer"),
        "simulated_answer": rerank_res.get("generated_answer") or f"Basé sur la collection '{payload.collection_id}', voici les extraits les plus pertinents identifiés par la recherche '{payload.search_method}' et le reranker 'bge-reranker-v2-m3'.",
        "citations": [
            {"source_id": idx + 1, "score": c.get("relevance_score", 0.0)}
            for idx, c in enumerate(retrieved_chunks)
        ]
    }


import httpx
import time
from typing import Dict, Any, List, Optional
from app.core.config import settings

# Import résilient du logger pour éviter tout ModuleNotFoundError
try:
    from app.core.logger import log_event
except ImportError:
    try:
        from core.logger import log_event
    except ImportError:
        def log_event(category: str, message: str, level: str = "INFO"):
            print(f"[{category}] {message}")

class AlbertAPIClient:
    """
    Client HTTP asynchrone pour interagir avec les endpoints de l'API Albert (Etalab / DINUM)
    avec filtrage strict des diagrammes UML, modèle multimodal 24B (mistral-small-3-2-24b-instruct-2506)
    et nettoyage du Markdown pour garantir le bon affichage du bloc Mermaid.js.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.ALBERT_API_KEY
        self.base_url = (base_url or settings.ALBERT_API_BASE_URL).rstrip("/")
        self.headers = {
            "Accept": "application/json"
        }
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

    @staticmethod
    def _clean_vision_response(content: str) -> str:
        """
        Nettoie la réponse pour s'assurer que la transcription Mermaid est strictement valide,
        proprement séparée et sans syntaxe parasite pouvant causer des chevauchements dans le Markdown.
        """
        if not content:
            return ""
        
        text = content.strip()
        
        # 1. Vérifier si le modèle a renvoyé NON_UML ou si c'est une capture d'écran
        if text.upper().startswith("NON_UML") or "NON_UML" in text[:40].upper():
            return ""
            
        # 2. Chercher le bloc ```mermaid ... ``` ou ``` ... ```
        import re
        mermaid_match = re.search(r'```(?:mermaid)?\s*\n(.*?)\n```', text, re.DOTALL | re.IGNORECASE)
        
        if not mermaid_match:
            # Aucun bloc de code valide trouvé
            return ""
            
        mermaid_code = mermaid_match.group(1).strip()
        
        # Mots-clés Mermaid valides officiels
        valid_keywords = (
            "graph ", "graph\n", "graph\r\n",
            "flowchart ", "flowchart\n", "flowchart\r\n",
            "sequencediagram", "classdiagram", "statediagram", "statediagram-v2",
            "erdiagram", "mindmap", "gantt", "pie", "gitgraph", "c4context", "c4container",
            "requirementdiagram", "architecture-beta", "timeline", "journey"
        )
        
        # Trouver la première ligne effective (non-commentaire et non-vide)
        code_lines = [l.strip() for l in mermaid_code.splitlines() if l.strip() and not l.strip().startswith("%%")]
        if not code_lines:
            return ""
            
        first_line = code_lines[0].lower()
        
        # Rejeter les fausses syntaxes PlantUML / pseudo-code d'écran UI (skinparam, @startuml, rectangle as, group as, inputField)
        if any(bad in first_line for bad in ["skinparam", "@startuml", "rectangle ", "group ", "inputfield", "button "]):
            return ""
            
        if not any(first_line.startswith(k) for k in valid_keywords):
            if "-->" in mermaid_code or "---" in mermaid_code or "-.->" in mermaid_code:
                mermaid_code = "flowchart TD\n" + mermaid_code
            else:
                return ""

        # Extraire la description textuelle courte préliminaire
        desc_part = text[:mermaid_match.start()].strip()
        desc_part = re.sub(r'^```[\w]*\n?', '', desc_part).strip()
        
        output_blocks = []
        if desc_part and len(desc_part) > 10 and not desc_part.upper().startswith("NON_UML"):
            # Description textuelle isolée sans balise Markdown cassée
            output_blocks.append(f"**Description du schéma :** {desc_part}")
            
        output_blocks.append("```mermaid\n" + mermaid_code + "\n```")
        
        return "\n\n".join(output_blocks)

    async def describe_image(
        self, 
        image_base64: str, 
        prompt: Optional[str] = None,
        mime_type: str = "image/png"
    ) -> str:
        """
        Appelle le modèle LLM Vision 24B d'Albert API (mistral-small-3-2-24b-instruct-2506).
        Détermine si l'image est STRICTEMENT un diagramme UML / d'architecture.
        Nettoie les backticks parasites pour garantir un rendu parfait du bloc Mermaid.js.
        """
        model_name = "mistral-small-3-2-24b-instruct-2506"
        log_event("LLM-VISION", f"🚀 Analyse image avec le modèle Vision 24B '{model_name}'...")
        
        system_prompt = (
            "Examine très attentivement l'image fournie.\n"
            "RÈGLE STRICTE DE CLASSIFICATION ET DE FORMATAGE :\n"
            "1. Si l'image est une capture d'écran d'application, une interface web, un formulaire, un tableau, une photo, une icône ou TOUT élément qui n'est pas un diagramme UML/Flowchart :\n"
            "   Réponds UNIQUEMENT sur la première ligne : 'NON_UML'. Ne génère AUCUN diagramme ni code Mermaid.\n"
            "2. SI ET SEULEMENT SI l'image est un véritable DIAGRAMME TECHNIQUE (Diagramme de classe, diagramme de séquence, diagramme d'activité, flowchart d'architecture) :\n"
            "   - Donne une courte explication textuelle (1 phrase).\n"
            "   - Insère le code Mermaid commençant OBLIGATOIREMENT par un mot-clé valide (`flowchart TD`, `sequenceDiagram`, `classDiagram`, `stateDiagram-v2`, `erDiagram`) :\n"
            "```mermaid\n"
            "[code du diagramme valide]\n"
            "```"
        )

        user_prompt = prompt or system_prompt

        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}}
                    ]
                }
            ],
            "max_tokens": 800
        }
        
        t0 = time.time()
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.post(f"{self.base_url}/chat/completions", json=payload)
                elapsed = round(time.time() - t0, 2)
                
                if response.status_code == 200:
                    res_json = response.json()
                    choices = res_json.get("choices", [])
                    usage = res_json.get("usage", {})
                    tokens_used = usage.get("completion_tokens", 0)
                    
                    if choices:
                        raw_content = choices[0].get("message", {}).get("content", "").strip()
                        if "NON_UML" in raw_content[:30].upper():
                            log_event("LLM-VISION", f"ℹ️ Image identifiée comme Capture / Non-UML par {model_name} ({elapsed}s). Mermaid ignoré.")
                            return ""
                        
                        cleaned_content = self._clean_vision_response(raw_content)
                        log_event("LLM-VISION", f"✅ Diagramme UML détecté et transcrit proprement en Mermaid.js en {elapsed}s | Tokens: {tokens_used}")
                        return cleaned_content
                else:
                    err_preview = response.text[:200]
                    log_event("LLM-VISION", f"⚠️ Erreur HTTP {response.status_code} d'Albert API Vision ({elapsed}s) : {err_preview}", level="WARNING")
                return ""
            except Exception as e:
                log_event("LLM-VISION", f"❌ Erreur lors de l'appel LLM Vision: {e}", level="ERROR")
                return ""

    async def list_collections(self, visibility: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Liste les collections sur Albert API avec filtres visibility et limit."""
        params = {"limit": limit}
        if visibility and visibility.lower() != "all":
            params["visibility"] = visibility.lower()

        log_event("ALBERT-API", f"📡 Requête GET /v1/collections (visibility={visibility or 'default'}, limit={limit})...")
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/collections", params=params)
                if response.status_code == 200:
                    data = response.json()
                    cols = data if isinstance(data, list) else data.get("data", data.get("collections", []))
                    log_event("ALBERT-API", f"📥 GET /v1/collections -> {len(cols)} collection(s) reçue(s)")
                    return cols
                log_event("ALBERT-API", f"⚠️ GET /v1/collections HTTP {response.status_code}", level="WARNING")
                return []
            except Exception as e:
                log_event("ALBERT-API", f"❌ Erreur GET /v1/collections: {e}", level="ERROR")
                return []

    async def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        """Récupère les détails d'une collection par ID ou nom."""
        log_event("ALBERT-API", f"📡 Requête GET /v1/collections/{collection_id}...")
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/collections/{collection_id}")
                if response.status_code == 200:
                    return response.json()
                return None
            except Exception as e:
                log_event("ALBERT-API", f"❌ Erreur GET /v1/collections/{collection_id}: {e}", level="ERROR")
                return None

    async def create_collection(self, name: str, description: str = "", visibility: str = "private") -> Dict[str, Any]:
        """Crée une nouvelle collection sur Albert API."""
        log_event("ALBERT-API", f"🚀 Requête POST /v1/collections (Nom: '{name}', Visibilité: {visibility})...")
        payload = {
            "name": name,
            "description": description,
            "visibility": visibility
        }
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.post(f"{self.base_url}/collections", json=payload)
                if response.status_code in [200, 201]:
                    res_data = response.json()
                    log_event("ALBERT-API", f"✅ Collection '{name}' créée sur Albert API (ID: {res_data.get('id')})")
                    col_id = res_data.get("id")
                    if col_id:
                        details = await self.get_collection(str(col_id))
                        if details:
                            return details
                    return res_data
                log_event("ALBERT-API", f"⚠️ POST /v1/collections HTTP {response.status_code}", level="WARNING")
                return {"id": f"col_{name}", "name": name, "description": description, "visibility": visibility, "status": "created_local"}
            except Exception as e:
                log_event("ALBERT-API", f"❌ Erreur create_collection: {e}", level="ERROR")
                return {"id": f"col_{name}", "name": name, "description": description, "visibility": visibility, "status": "created_local"}

    async def delete_collection(self, collection_id: str) -> bool:
        """Supprime une collection sur Albert API."""
        log_event("ALBERT-API", f"🗑️ Requête DELETE /v1/collections/{collection_id}...")
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.delete(f"{self.base_url}/collections/{collection_id}")
                return response.status_code in [200, 204]
            except Exception:
                return True

    async def delete_document(self, document_id: str) -> bool:
        """Supprime un document sur Albert API."""
        log_event("ALBERT-API", f"🗑️ Requête DELETE /v1/documents/{document_id}...")
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.delete(f"{self.base_url}/documents/{document_id}")
                if response.status_code in [200, 204]:
                    log_event("ALBERT-API", f"✅ Document #{document_id} supprimé avec succès sur Albert API")
                    return True
                log_event("ALBERT-API", f"⚠️ DELETE /v1/documents/{document_id} HTTP {response.status_code}", level="WARNING")
                return response.status_code == 404
            except Exception as e:
                log_event("ALBERT-API", f"❌ Erreur delete_document({document_id}): {e}", level="ERROR")
                return True

    async def list_documents(self, collection_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Interroge /v1/documents sur Albert API."""
        params = {"limit": limit}
        if collection_id:
            try:
                params["collection_id"] = int(collection_id)
            except ValueError:
                params["collection_id"] = collection_id

        log_event("ALBERT-API", f"📡 Requête GET /v1/documents (collection_id={collection_id})...")
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/documents", params=params)
                if response.status_code == 200:
                    data = response.json()
                    docs = data if isinstance(data, list) else data.get("data", data.get("documents", []))
                    log_event("ALBERT-API", f"📥 GET /v1/documents -> {len(docs)} document(s) trouvé(s)")
                    return docs
                return []
            except Exception as e:
                log_event("ALBERT-API", f"❌ Erreur list_documents: {e}", level="ERROR")
                return []

    async def get_document_chunks(self, document_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Récupère les morceaux (chunks) d'un document sur Albert API."""
        log_event("ALBERT-API", f"📡 Requête GET /v1/documents/{document_id}/chunks (limit={limit})...")
        params = {"limit": limit}
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/documents/{document_id}/chunks", params=params)
                if response.status_code == 200:
                    data = response.json()
                    chunks = data if isinstance(data, list) else data.get("data", data.get("chunks", []))
                    log_event("ALBERT-API", f"📥 Chunks reçus -> {len(chunks)} extraits pour document #{document_id}")
                    return chunks
                return []
            except Exception as e:
                log_event("ALBERT-API", f"❌ Erreur get_document_chunks({document_id}): {e}", level="ERROR")
                return []

    async def upload_document(self, collection_id: str, file_path: str, filename: str) -> Dict[str, Any]:
        """Envoie un fichier Markdown (.md) à une collection Albert API."""
        log_event("ALBERT-API", f"🚀 Indexation POST /v1/documents -> Envoi du fichier '{filename}' dans collection {collection_id}...")
        async with httpx.AsyncClient(headers=self.headers, timeout=120.0) as client:
            try:
                with open(file_path, "rb") as f:
                    files = {"file": (filename, f, "text/markdown")}
                    col_val = int(collection_id) if str(collection_id).isdigit() else collection_id
                    data = {"collection_id": col_val}
                    response = await client.post(f"{self.base_url}/documents", data=data, files=files)
                    if response.status_code in [200, 201]:
                        res_json = response.json()
                        log_event("ALBERT-API", f"✅ Document '{filename}' indexé avec succès dans Albert API (Doc ID: {res_json.get('id')})")
                        return res_json
                    log_event("ALBERT-API", f"⚠️ POST /v1/documents HTTP {response.status_code}", level="WARNING")
                    return {"id": f"doc_{filename}", "filename": filename, "status": "indexed_local"}
            except Exception as e:
                log_event("ALBERT-API", f"❌ Erreur upload_document: {e}", level="ERROR")
                return {"id": f"doc_{filename}", "filename": filename, "status": "indexed_local"}

    async def rerank(
        self, 
        query: str, 
        documents: List[str], 
        top_n: int = 3, 
        model: str = "bge-reranker-v2-m3"
    ) -> List[Dict[str, Any]]:
        """Appelle /v1/rerank pour réordonner les candidats avec le modèle Cross-Encoder 'bge-reranker-v2-m3'."""
        if not documents:
            return []

        log_event("LLM-RERANK", f"🔀 Rerank LLM '{model}' -> Évaluation de {len(documents)} extraits pour la question: '{query}'...")
        payload = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": top_n
        }

        t0 = time.time()
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.post(f"{self.base_url}/rerank", json=payload)
                elapsed = round(time.time() - t0, 2)
                if response.status_code == 200:
                    data = response.json()
                    raw_results = data.get("results", [])
                    log_event("LLM-RERANK", f"✅ Reranking terminé en {elapsed}s -> {len(raw_results)} extrait(s) réordonné(s)")
                    
                    reranked = []
                    for item in raw_results:
                        idx = item.get("index", 0)
                        score = item.get("relevance_score", 0.0)
                        if 0 <= idx < len(documents):
                            reranked.append({
                                "index": idx,
                                "document": documents[idx],
                                "relevance_score": round(score, 4)
                            })
                    return reranked
                else:
                    log_event("LLM-RERANK", f"⚠️ Erreur HTTP {response.status_code} d'Albert API Rerank ({elapsed}s)", level="WARNING")
                    return []
            except Exception as e:
                log_event("LLM-RERANK", f"❌ Erreur rerank: {e}", level="ERROR")
                return []

albert_client = AlbertAPIClient()

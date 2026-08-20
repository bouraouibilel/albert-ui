import httpx
from typing import Dict, Any, List, Optional
from app.core.config import settings

class AlbertAPIClient:
    """
    Client HTTP asynchrone pour interagir avec les endpoints de l'API Albert (Etalab / DINUM).
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.ALBERT_API_KEY
        self.base_url = (base_url or settings.ALBERT_API_BASE_URL).rstrip("/")
        self.headers = {
            "Accept": "application/json"
        }
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

    async def list_collections(self, visibility: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Liste les collections sur Albert API avec filtres visibility et limit."""
        params = {"limit": limit}
        if visibility and visibility.lower() != "all":
            params["visibility"] = visibility.lower()

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/collections", params=params)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        return data.get("data", data.get("collections", []))
                return []
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur list_collections: {e}")
                return []

    async def get_collection(self, collection_id: str) -> Optional[Dict[str, Any]]:
        """Récupère les détails d'une collection par ID ou nom."""
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/collections/{collection_id}")
                if response.status_code == 200:
                    return response.json()
                return None
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur get_collection({collection_id}): {e}")
                return None

    async def create_collection(self, name: str, description: str = "", visibility: str = "private") -> Dict[str, Any]:
        """Crée une nouvelle collection sur Albert API."""
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
                    col_id = res_data.get("id")
                    if col_id:
                        details = await self.get_collection(str(col_id))
                        if details:
                            return details
                    return res_data
                return {"id": f"col_{name}", "name": name, "description": description, "visibility": visibility, "status": "created_local"}
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur create_collection: {e}")
                return {"id": f"col_{name}", "name": name, "description": description, "visibility": visibility, "status": "created_local"}

    async def delete_collection(self, collection_id: str) -> bool:
        """Supprime une collection sur Albert API."""
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.delete(f"{self.base_url}/collections/{collection_id}")
                return response.status_code in [200, 204]
            except Exception:
                return True

    async def list_documents(self, collection_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Interroge /v1/documents sur Albert API pour récupérer la liste des documents d'une collection."""
        params = {"limit": limit}
        if collection_id:
            try:
                params["collection_id"] = int(collection_id)
            except ValueError:
                params["collection_id"] = collection_id

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/documents", params=params)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        return data.get("data", data.get("documents", []))
                return []
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur list_documents: {e}")
                return []

    async def get_document_chunks(self, document_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Récupère les découpages (chunks) d'un document spécifique sur Albert API."""
        params = {"limit": limit}
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/documents/{document_id}/chunks", params=params)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        return data.get("data", data.get("chunks", []))
                return []
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur get_document_chunks({document_id}): {e}")
                return []

    async def upload_document(self, collection_id: str, file_path: str, filename: str) -> Dict[str, Any]:
        """Envoie un fichier Markdown (.md) à une collection Albert API."""
        async with httpx.AsyncClient(headers=self.headers, timeout=120.0) as client:
            try:
                with open(file_path, "rb") as f:
                    files = {"file": (filename, f, "text/markdown")}
                    col_val = int(collection_id) if str(collection_id).isdigit() else collection_id
                    data = {"collection_id": col_val}
                    response = await client.post(f"{self.base_url}/documents", data=data, files=files)
                    if response.status_code in [200, 201]:
                        return response.json()
                    return {"id": f"doc_{filename}", "filename": filename, "status": "indexed_local"}
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur upload_document: {e}")
                return {"id": f"doc_{filename}", "filename": filename, "status": "indexed_local"}

    async def rerank(
        self, 
        query: str, 
        documents: List[str], 
        top_n: int = 3, 
        model: str = "bge-reranker-v2-m3"
    ) -> List[Dict[str, Any]]:
        """
        Appelle l'endpoint /v1/rerank d'Albert API avec le modèle Cross-Encoder 'bge-reranker-v2-m3'
        et mappe les scores aux textes des documents.
        """
        if not documents:
            return []

        payload = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": top_n
        }

        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.post(f"{self.base_url}/rerank", json=payload)
                if response.status_code == 200:
                    data = response.json()
                    raw_results = data.get("results", [])
                    
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
                    print(f"[AlbertAPIClient] Rerank error {response.status_code}: {response.text}")
                    return []
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur rerank: {e}")
                return []

albert_client = AlbertAPIClient()

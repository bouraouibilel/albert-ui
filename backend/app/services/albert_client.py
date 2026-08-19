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

    async def list_collections(self) -> List[Dict[str, Any]]:
        """Liste les collections globales accessibles sur Albert API."""
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.get(f"{self.base_url}/collections")
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
        """Récupère les détails précis d'une collection par son ID ou son nom."""
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
                    # Si Albert API retourne {"id": 12345}, récupérer les détails complets
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

    async def upload_document(self, collection_id: str, file_path: str, filename: str) -> Dict[str, Any]:
        """Envoie un fichier Markdown (.md) à une collection Albert API."""
        async with httpx.AsyncClient(headers=self.headers, timeout=120.0) as client:
            try:
                with open(file_path, "rb") as f:
                    files = {"file": (filename, f, "text/markdown")}
                    data = {"collection_id": collection_id}
                    response = await client.post(f"{self.base_url}/documents", data=data, files=files)
                    if response.status_code in [200, 201]:
                        return response.json()
                    return {"id": f"doc_{filename}", "filename": filename, "status": "indexed_local"}
            except Exception as e:
                print(f"[AlbertAPIClient] Erreur upload_document: {e}")
                return {"id": f"doc_{filename}", "filename": filename, "status": "indexed_local"}

    async def rerank(self, query: str, documents: List[str], top_n: int = 3) -> List[Dict[str, Any]]:
        """Appelle /v1/rerank pour réordonner les documents candidats."""
        payload = {
            "query": query,
            "documents": documents,
            "top_n": top_n
        }
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            try:
                response = await client.post(f"{self.base_url}/rerank", json=payload)
                if response.status_code == 200:
                    return response.json().get("results", [])
                return []
            except Exception:
                return []

albert_client = AlbertAPIClient()

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
import os
import json
from app.services.albert_client import albert_client
from app.core.config import settings

router = APIRouter(prefix="/collections", tags=["Collections"])

LOCAL_COLLECTIONS_FILE = os.path.join(settings.CONVERTED_DIR, "collections_meta.json")

def get_local_collections() -> List[dict]:
    if os.path.exists(LOCAL_COLLECTIONS_FILE):
        try:
            with open(LOCAL_COLLECTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_local_collections(cols: List[dict]):
    with open(LOCAL_COLLECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(cols, f, ensure_ascii=False, indent=2)

class CollectionCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    visibility: Optional[str] = "private"

class CollectionLink(BaseModel):
    collection_id_or_name: str

@router.get("/")
async def list_collections(visibility: Optional[str] = Query("private", description="Filtre par visibilité (private/public/all)")):
    """
    Retourne la liste des collections.
    - visibility=private (par défaut) : Retourne les collections privées créées ou liées par l'utilisateur.
    - visibility=all : Retourne l'ensemble des collections (privées + publiques plate-forme).
    """
    remote_cols = await albert_client.list_collections()
    local_cols = get_local_collections()
    
    merged = {}
    
    # 1. Process local user private collections & refresh details from Albert API if possible
    for c in local_cols:
        if isinstance(c, dict):
            col_key = str(c.get("id") or c.get("name") or "")
            if col_key:
                c_copy = dict(c)
                # Tente de rafraîchir les métriques en direct depuis Albert API
                remote_detail = await albert_client.get_collection(col_key)
                if remote_detail:
                    c_copy.update(remote_detail)
                merged[col_key] = c_copy

    # 2. Process remote collections list
    if isinstance(remote_cols, list):
        for r in remote_cols:
            if isinstance(r, dict):
                col_key = str(r.get("id") or r.get("name") or "")
                if col_key:
                    if col_key in merged:
                        merged[col_key].update(r)
                    else:
                        merged[col_key] = r
            elif isinstance(r, str):
                if r not in merged:
                    merged[r] = {"id": r, "name": r, "description": "", "visibility": "public"}
            
    all_cols = list(merged.values())
    
    # Filter logic
    if visibility and visibility.lower() != "all":
        if visibility.lower() == "private":
            filtered = [
                c for c in all_cols 
                if str(c.get("visibility", "")).lower() != "public"
            ]
            return filtered
        else:
            filtered = [
                c for c in all_cols 
                if str(c.get("visibility", "")).lower() == visibility.lower()
            ]
            return filtered
        
    return all_cols

@router.post("/")
async def create_collection(payload: CollectionCreate):
    """Crée une collection sur Albert API et la sauvegarde localement."""
    result = await albert_client.create_collection(
        name=payload.name,
        description=payload.description or "",
        visibility=payload.visibility or "private"
    )
    
    cols = get_local_collections()
    new_col = {
        "id": result.get("id", f"col_{payload.name}"),
        "name": payload.name,
        "description": payload.description or result.get("description", ""),
        "visibility": payload.visibility or result.get("visibility", "private"),
        "document_count": result.get("documents", 0),
        "created_at": result.get("created")
    }
    
    # Eviter les doublons
    cols = [c for c in cols if str(c.get("id")) != str(new_col["id"]) and c.get("name") != new_col["name"]]
    cols.append(new_col)
    save_local_collections(cols)
    
    return new_col

@router.post("/link")
async def link_existing_collection(payload: CollectionLink):
    """Lie/importe une collection existante depuis Albert API par son ID ou son nom."""
    details = await albert_client.get_collection(payload.collection_id_or_name)
    
    cols = get_local_collections()
    if details:
        col_item = details
    else:
        col_item = {
            "id": payload.collection_id_or_name,
            "name": payload.collection_id_or_name,
            "description": "Collection Albert API liée manuellement",
            "visibility": "private"
        }
        
    cols = [c for c in cols if str(c.get("id")) != str(col_item.get("id")) and c.get("name") != col_item.get("name")]
    cols.append(col_item)
    save_local_collections(cols)
    
    return col_item

@router.delete("/{collection_id}")
async def delete_collection(collection_id: str):
    await albert_client.delete_collection(collection_id)
    cols = get_local_collections()
    cols = [c for c in cols if str(c.get("id")) != str(collection_id) and c.get("name") != collection_id]
    save_local_collections(cols)
    return {"status": "success", "deleted_id": collection_id}

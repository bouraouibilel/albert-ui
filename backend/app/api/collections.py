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
    Par défaut (visibility=private), interroge Albert API avec params={"visibility": "private", "limit": 100}
    pour renvoyer vos collections privées (ex: PASRAU).
    """
    remote_cols = await albert_client.list_collections(visibility=visibility, limit=100)
    local_cols = get_local_collections()
    
    merged = {}
    
    # 1. Process remote collections directly from Albert API
    if isinstance(remote_cols, list):
        for r in remote_cols:
            if isinstance(r, dict):
                col_key = str(r.get("name") or r.get("id") or "")
                if col_key:
                    merged[col_key] = r

    # 2. Merge local collections
    for c in local_cols:
        if isinstance(c, dict):
            col_key = str(c.get("name") or c.get("id") or "")
            if col_key:
                if col_key not in merged:
                    merged[col_key] = c
                else:
                    merged[col_key].update(c)

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
    
    cols = [c for c in cols if str(c.get("id")) != str(new_col["id"]) and c.get("name") != new_col["name"]]
    cols.append(new_col)
    save_local_collections(cols)
    
    return new_col

@router.post("/link")
async def link_existing_collection(payload: CollectionLink):
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

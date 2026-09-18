from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import hmac
import hashlib
import time
from app.core.config import settings
from app.core.logger import log_event

router = APIRouter(prefix="/auth", tags=["Authentification Admin"])

class LoginRequest(BaseModel):
    username: str
    password: str

def generate_admin_token(username: str) -> str:
    """Génère un jeton sécurisé basé sur HMAC avec la clé secrète."""
    ts = str(int(time.time()))
    raw = f"{username.strip().lower()}:{ts}"
    signature = hmac.new(settings.SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}:{signature}"

def verify_admin_token(token: str) -> bool:
    """Vérifie la signature et la validité temporelle (24h) du jeton."""
    if not token:
        return False
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return False
        username, ts_str, signature = parts
        
        # Vérifier HMAC signature
        expected_sig = hmac.new(settings.SECRET_KEY.encode("utf-8"), f"{username}:{ts_str}".encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return False
            
        # Expire après 24 heures (86400s)
        token_time = int(ts_str)
        if time.time() - token_time > 86400:
            return False
            
        return username == settings.ADMIN_USERNAME.strip().lower()
    except Exception:
        return False

@router.post("/login")
async def login(payload: LoginRequest):
    """
    Endpoint de connexion sécurisé pour l'interface d'administration Albert RAG.
    """
    input_user = payload.username.strip().lower()
    input_pass = payload.password.strip()
    target_user = settings.ADMIN_USERNAME.strip().lower()
    target_pass = settings.ADMIN_PASSWORD.strip()

    if input_user == target_user and input_pass == target_pass:
        token = generate_admin_token(input_user)
        log_event("AUTH", f"🔑 Connexion réussie de l'administrateur '{input_user}'")
        return {
            "status": "success",
            "message": "Authentification réussie",
            "token": token,
            "username": input_user
        }
    else:
        log_event("AUTH", f"⚠️ Échec de connexion pour l'utilisateur '{payload.username}'", level="WARNING")
        raise HTTPException(status_code=401, detail="Identifiant ou mot de passe incorrect.")

@router.get("/verify")
async def verify(token: str):
    """Vérifie si le jeton d'authentification est valide."""
    is_valid = verify_admin_token(token)
    return {"valid": is_valid}

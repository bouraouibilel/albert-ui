import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Charge les variables d'environnement depuis backend/.env si présent
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

class Settings(BaseSettings):
    PROJECT_NAME: str = "Albert RAG Admin API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Configuration API Albert
    ALBERT_API_BASE_URL: str = os.getenv("ALBERT_API_BASE_URL", "https://albert.api.etalab.gouv.fr/v1")
    ALBERT_API_KEY: str = os.getenv("ALBERT_API_KEY", "ALBERT_API_KEY")
    
    # Répertoires de stockage local
    UPLOAD_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "uploads")
    CONVERTED_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "converted")

    class Config:
        case_sensitive = True

settings = Settings()

# Assurance de la création des répertoires
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.CONVERTED_DIR, exist_ok=True)

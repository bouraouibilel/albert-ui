import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Charge explicitement les variables d'environnement du fichier backend/.env
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path, override=True)

class Settings(BaseSettings):
    PROJECT_NAME: str = "Albert RAG Admin API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    ALBERT_API_BASE_URL: str = os.getenv("ALBERT_API_BASE_URL", "https://albert.api.etalab.gouv.fr/v1")
    ALBERT_API_KEY: str = os.getenv("ALBERT_API_KEY", "")
    
    UPLOAD_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "uploads")
    CONVERTED_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "converted")
    IMAGE_STORAGE_DIR: str = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "images")
    IMAGE_BASE_URL: str = os.getenv("IMAGE_BASE_URL", "http://localhost:8000/static/images")

    model_config = SettingsConfigDict(
        env_file=env_path if os.path.exists(env_path) else None,
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# Garantit que la clé réelle du .env est bien assignée
if not settings.ALBERT_API_KEY or settings.ALBERT_API_KEY == "ALBERT_API_KEY":
    settings.ALBERT_API_KEY = os.getenv("ALBERT_API_KEY", "")

# Assurance de la création des répertoires de stockage
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.CONVERTED_DIR, exist_ok=True)
os.makedirs(settings.IMAGE_STORAGE_DIR, exist_ok=True)

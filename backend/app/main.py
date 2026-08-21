from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import os
from app.core.config import settings
from app.api import collections, documents, rag

# Import résilient du logger
try:
    from app.core.logger import get_recent_logs
except ImportError:
    def get_recent_logs():
        return []

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="API d'Administration pour l'ingestion, la pré-conversion Markdown (.md), et le RAG Albert API (DINUM/Etalab) pour Open WebUI"
)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir le dossier des images stockées sous /static/images/ pour récupération par Open WebUI / LLM
app.mount("/static/images", StaticFiles(directory=settings.IMAGE_STORAGE_DIR), name="static_images")

# Inclusion des routeurs API
app.include_router(collections.router, prefix=settings.API_V1_STR)
app.include_router(documents.router, prefix=settings.API_V1_STR)
app.include_router(rag.router, prefix=settings.API_V1_STR)

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "index.html")

@app.get("/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
async def get_admin_ui():
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Albert RAG Admin API</h1><p>Interface indisponible.</p>"

@app.get("/api/health")
async def health_check():
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
        "albert_api_configured": bool(settings.ALBERT_API_KEY),
        "image_base_url": settings.IMAGE_BASE_URL
    }

@app.get(f"{settings.API_V1_STR}/logs")
async def get_logs():
    """Endpoint de consultation du journal des événements et des appels LLM en temps réel."""
    return get_recent_logs()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

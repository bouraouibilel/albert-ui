import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api import collections, documents, rag, watchers, auth, stats
from app.services.watcher_service import watcher_service

# Import résilient du logger et du consommomètre utilisateur
try:
    from app.core.logger import get_recent_logs, record_user_request
except ImportError:
    def get_recent_logs():
        return []
    def record_user_request(*args, **kwargs):
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Démarrage du listener de surveillance automatique des dossiers en arrière-plan
    await watcher_service.start_background_listener()
    yield
    # Arrêt gracieux du listener
    await watcher_service.stop_background_listener()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="API d'Administration pour l'ingestion, la pré-conversion Markdown (.md), et le RAG Albert API (DINUM/Etalab) pour Open WebUI",
    lifespan=lifespan,
    redirect_slashes=False
)

# Configuration CORS pour autoriser l'ensemble des domaines et reverse proxies (NGINX / MNS / F5)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def track_user_requests_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_header = request.headers.get("x-user-id") or request.headers.get("x-user-email") or request.headers.get("x-user")
    auth_header = request.headers.get("authorization", "")

    user_id = "admin"
    if user_header:
        user_id = user_header.strip()
    elif auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "").strip()
        parts = token.split(":")
        if len(parts) >= 1 and parts[0]:
            user_id = parts[0].strip()
    else:
        user_id = f"Client ({client_ip})"

    path = request.url.path
    if not path.startswith("/static") and path != "/favicon.ico":
        is_llm = "rerank" in path or "vision" in path or "chat" in path
        record_user_request(user_id=user_id, endpoint=path, is_llm=is_llm, ip=client_ip)

    response = await call_next(request)
    return response

STYLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "style")

# Servir le dossier des images stockées sous /static/images/ pour récupération par Open WebUI / LLM
app.mount("/static/images", StaticFiles(directory=settings.IMAGE_STORAGE_DIR), name="static_images")
if os.path.exists(STYLE_DIR):
    app.mount("/static/style", StaticFiles(directory=STYLE_DIR), name="static_style")

# Inclusion des routeurs API
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(collections.router, prefix=settings.API_V1_STR)
app.include_router(documents.router, prefix=settings.API_V1_STR)
app.include_router(rag.router, prefix=settings.API_V1_STR)
app.include_router(watchers.router, prefix=settings.API_V1_STR)
app.include_router(stats.router, prefix=settings.API_V1_STR)

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

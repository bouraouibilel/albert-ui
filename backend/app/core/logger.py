import logging
import sys
from typing import List

# Force l'encodage UTF-8 pour sys.stdout sur Windows CMD / PowerShell
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Configuration du format des logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("albert_admin")

import datetime
LOG_HISTORY: List[dict] = []

def log_event(category: str, message: str, level: str = "INFO"):
    formatted_msg = f"[{category}] {message}"
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    try:
        if level == "ERROR":
            logger.error(formatted_msg)
        elif level == "WARNING":
            logger.warning(formatted_msg)
        else:
            logger.info(formatted_msg)
    except Exception:
        # Fallback si l'encodage de la console hôte bloque
        clean_msg = formatted_msg.encode("ascii", "ignore").decode("ascii")
        logger.info(clean_msg)
        
    LOG_HISTORY.append({
        "timestamp": timestamp,
        "category": category,
        "message": message,
        "level": level
    })
    if len(LOG_HISTORY) > 200:
        LOG_HISTORY.pop(0)

USER_USAGE_STATS: dict = {}

def record_user_request(user_id: str, endpoint: str = "", is_llm: bool = False, ip: str = ""):
    """Enregistre la consommation de requêtes par utilisateur ou client IP."""
    if not user_id:
        user_id = f"Client ({ip or '127.0.0.1'})"
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if user_id not in USER_USAGE_STATS:
        USER_USAGE_STATS[user_id] = {
            "user_id": user_id,
            "ip": ip or "127.0.0.1",
            "total_requests": 0,
            "llm_calls": 0,
            "rag_queries": 0,
            "last_active": timestamp
        }
    
    stats = USER_USAGE_STATS[user_id]
    stats["total_requests"] += 1
    stats["last_active"] = timestamp
    if is_llm or "rerank" in endpoint or "vision" in endpoint or "chat" in endpoint:
        stats["llm_calls"] += 1
    if "rag" in endpoint:
        stats["rag_queries"] += 1

def get_user_usage_stats() -> List[dict]:
    """Retourne la liste des statistiques de consommation d'utilisation triée par volume de requêtes."""
    return sorted(list(USER_USAGE_STATS.values()), key=lambda x: x["total_requests"], reverse=True)

def get_recent_logs() -> List[dict]:
    return LOG_HISTORY

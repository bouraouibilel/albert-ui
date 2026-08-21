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

LOG_HISTORY: List[dict] = []

def log_event(category: str, message: str, level: str = "INFO"):
    formatted_msg = f"[{category}] {message}"
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
        "category": category,
        "message": message,
        "level": level
    })
    if len(LOG_HISTORY) > 100:
        LOG_HISTORY.pop(0)

def get_recent_logs() -> List[dict]:
    return LOG_HISTORY

import os
import sys
import json
import uuid
import time
import asyncio
import fnmatch
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

from app.core.config import settings
from app.core.logger import log_event
from app.services.converter import DocumentConverter
from app.services.albert_client import albert_client

WATCHERS_CONFIG_FILE = os.path.join(settings.STORAGE_DIR, "watchers_config.json")
WATCHERS_HISTORY_FILE = os.path.join(settings.STORAGE_DIR, "watchers_history.json")
SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.html', '.htm', '.txt', '.md'}

class WatcherService:
    """
    Service d'écoute automatique en arrière-plan pour surveiller un ou plusieurs répertoires (un par collection),
    détecter immédiatement TOUS les nouveaux documents et les traiter de manière concurrente et non-bloquante.
    """
    def __init__(self):
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._scan_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(2)  # Jusqu'à 2 conversions en parallèle pour ne pas saturer l'API
        self._active_file_keys: Set[Tuple[str, int, int]] = set()
        self._active_task_ids: Set[str] = set()
        self._ensure_storage_files()

    def _ensure_storage_files(self):
        os.makedirs(settings.STORAGE_DIR, exist_ok=True)
        if not os.path.exists(WATCHERS_CONFIG_FILE):
            with open(WATCHERS_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)
        if not os.path.exists(WATCHERS_HISTORY_FILE):
            with open(WATCHERS_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)

    def get_watchers(self) -> List[Dict[str, Any]]:
        self._ensure_storage_files()
        try:
            with open(WATCHERS_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_event("WATCHER", f"⚠️ Erreur lecture config watchers: {e}", level="WARNING")
            return []

    def save_watchers(self, watchers: List[Dict[str, Any]]):
        self._ensure_storage_files()
        with open(WATCHERS_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(watchers, f, indent=2, ensure_ascii=False)

    def get_history(self, status: Optional[str] = None, collection_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        self._ensure_storage_files()
        try:
            with open(WATCHERS_HISTORY_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
            
            if status and status.lower() != "all":
                items = [i for i in items if i.get("status") == status.lower()]
            if collection_id:
                items = [i for i in items if str(i.get("collection_id")) == str(collection_id)]
                
            items.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
            return items[:limit]
        except Exception as e:
            log_event("WATCHER", f"⚠️ Erreur lecture historique watchers: {e}", level="WARNING")
            return []

    def save_history(self, history: List[Dict[str, Any]]):
        self._ensure_storage_files()
        with open(WATCHERS_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

    def _update_history_item(self, item_id: str, updates: Dict[str, Any]):
        history = self.get_history(limit=500)
        found = False
        for item in history:
            if item.get("id") == item_id:
                item.update(updates)
                found = True
                break
        if not found and "id" in updates:
            history.insert(0, updates)
        self.save_history(history)

    def create_or_update_watcher(
        self, 
        collection_id: str, 
        collection_name: str, 
        folder_path: str, 
        filter_pattern: str = "*", 
        enabled: bool = True, 
        recursive: bool = False,
        watcher_id: Optional[str] = None
    ) -> Dict[str, Any]:
        watchers = self.get_watchers()
        clean_path = os.path.normpath(folder_path.strip())
        
        # Créer le répertoire physique s'il n'existe pas
        try:
            os.makedirs(clean_path, exist_ok=True)
        except Exception as e:
            log_event("WATCHER", f"⚠️ Impossible de créer le dossier surveillé '{clean_path}': {e}", level="WARNING")

        if not watcher_id:
            watcher_id = f"watch_{str(uuid.uuid4())[:8]}"
            watcher = {
                "id": watcher_id,
                "collection_id": str(collection_id),
                "collection_name": collection_name or f"Collection {collection_id}",
                "folder_path": clean_path,
                "filter_pattern": filter_pattern or "*",
                "enabled": bool(enabled),
                "recursive": bool(recursive),
                "created_at": datetime.datetime.now().isoformat(),
                "last_scan_at": None,
                "total_processed": 0
            }
            watchers.append(watcher)
            log_event("WATCHER", f"📁 Nouveau dossier d'écoute créé : '{clean_path}' -> Collection '{collection_name}' (ID: {collection_id})")
        else:
            watcher = None
            for w in watchers:
                if w.get("id") == watcher_id:
                    w.update({
                        "collection_id": str(collection_id),
                        "collection_name": collection_name or w.get("collection_name"),
                        "folder_path": clean_path,
                        "filter_pattern": filter_pattern or "*",
                        "enabled": bool(enabled),
                        "recursive": bool(recursive)
                    })
                    watcher = w
                    break
            if not watcher:
                raise ValueError(f"Watcher #{watcher_id} introuvable")
            log_event("WATCHER", f"✏️ Dossier d'écoute #{watcher_id} mis à jour : '{clean_path}'")

        self.save_watchers(watchers)
        # Déclencher un scan asynchrone immédiat après création/mise à jour
        asyncio.create_task(self.scan_all_folders())
        return watcher

    def delete_watcher(self, watcher_id: str) -> bool:
        watchers = self.get_watchers()
        new_watchers = [w for w in watchers if w.get("id") != watcher_id]
        if len(new_watchers) != len(watchers):
            self.save_watchers(new_watchers)
            log_event("WATCHER", f"🗑️ Dossier d'écoute #{watcher_id} supprimé")
            return True
        return False

    def toggle_watcher(self, watcher_id: str) -> Optional[Dict[str, Any]]:
        watchers = self.get_watchers()
        target = None
        for w in watchers:
            if w.get("id") == watcher_id:
                w["enabled"] = not w.get("enabled", True)
                target = w
                break
        if target:
            self.save_watchers(watchers)
            status_txt = "ACTIVÉ" if target["enabled"] else "EN PAUSE"
            log_event("WATCHER", f"🔄 Dossier d'écoute #{watcher_id} ({target.get('collection_name')}) -> {status_txt}")
            if target["enabled"]:
                asyncio.create_task(self.scan_all_folders())
        return target

    def delete_history_item(self, item_id: str) -> bool:
        history = self.get_history(limit=500)
        new_history = [h for h in history if h.get("id") != item_id]
        if len(new_history) != len(history):
            self.save_history(new_history)
            return True
        return False

    async def start_background_listener(self):
        """Démarre la boucle de surveillance périodique en arrière-plan."""
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._listener_loop())
        log_event("WATCHER", "🟢 Service Listener d'écoute automatique des répertoires démarré (Fréquence : 2.5s).")

    async def stop_background_listener(self):
        """Arrête la boucle de surveillance."""
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log_event("WATCHER", "🔴 Service Listener arrêté.")

    async def _listener_loop(self):
        """Boucle asynchrone continue effectuant un scan ultra-rapide toutes les 2.5 secondes."""
        while self._is_running:
            try:
                await self.scan_all_folders()
            except Exception as e:
                log_event("WATCHER", f"⚠️ Erreur lors du cycle de surveillance: {e}", level="WARNING")
            
            await asyncio.sleep(2.5)

    async def scan_all_folders(self) -> int:
        """
        Parcourt tous les répertoires actifs, détecte TOUS les nouveaux documents instantanément,
        les inscrit immédiatement dans l'historique en statut 'processing' et lance leur traitement
        en arrière-plan via une file asynchrone non-bloquante.
        """
        async with self._scan_lock:
            watchers = self.get_watchers()
            active_watchers = [w for w in watchers if w.get("enabled", True)]
            if not active_watchers:
                return 0

            history = self.get_history(limit=500)
            
            # Indexer les fichiers déjà connus
            known_files = {}
            for h in history:
                key = (os.path.normcase(os.path.normpath(h.get("file_path", ""))), h.get("file_mtime"), h.get("file_size"))
                known_files[key] = h.get("status")

            new_detected_items = []
            now_str = datetime.datetime.now().isoformat()

            for watcher in active_watchers:
                folder_path = watcher.get("folder_path")
                if not folder_path or not os.path.exists(folder_path):
                    continue

                filter_pattern = watcher.get("filter_pattern") or "*"
                recursive = watcher.get("recursive", False)
                collection_id = str(watcher.get("collection_id"))
                collection_name = watcher.get("collection_name") or f"Collection {collection_id}"
                watcher_id = watcher.get("id")

                watcher["last_scan_at"] = now_str

                path_obj = Path(folder_path)
                try:
                    file_iterator = list(path_obj.rglob("*") if recursive else path_obj.glob("*"))
                except Exception as e:
                    log_event("WATCHER", f"⚠️ Erreur parcours dossier '{folder_path}': {e}", level="WARNING")
                    continue

                for f in file_iterator:
                    if not f.is_file():
                        continue

                    # Ignorer les fichiers temporaires Word / lock ou fichiers .tmp
                    if f.name.startswith("~$") or f.name.startswith(".") or f.suffix.lower() == ".tmp":
                        continue

                    if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue

                    # Vérifier le filtre par nom (glob ou sous-chaîne)
                    name = f.name
                    if not (fnmatch.fnmatch(name.lower(), filter_pattern.lower()) or (filter_pattern.lower() in name.lower())):
                        continue

                    try:
                        stat = f.stat()
                        file_mtime = int(stat.st_mtime)
                        file_size = stat.st_size
                        resolved_path = str(f.resolve())
                        norm_path = os.path.normcase(os.path.normpath(resolved_path))
                    except Exception:
                        continue

                    file_key = (norm_path, file_mtime, file_size)

                    # Si déjà traité avec succès ou déjà activement en cours de traitement
                    if file_key in self._active_file_keys:
                        continue
                    if file_key in known_files and known_files[file_key] in ["completed", "processing"]:
                        continue

                    # Fichier nouveau détecté !
                    item_id = f"item_{str(uuid.uuid4())[:8]}"
                    self._active_file_keys.add(file_key)
                    self._active_task_ids.add(item_id)

                    history_item = {
                        "id": item_id,
                        "watcher_id": watcher_id,
                        "collection_id": str(collection_id),
                        "collection_name": collection_name,
                        "file_path": resolved_path,
                        "filename": f.name,
                        "file_size": file_size,
                        "file_mtime": file_mtime,
                        "status": "processing",
                        "progress_step": "En file d'attente...",
                        "error_message": None,
                        "albert_document_id": None,
                        "markdown_file": None,
                        "detected_at": now_str,
                        "started_at": now_str,
                        "completed_at": None,
                        "char_count": 0,
                        "pages_count": 1,
                        "tables_count": 0
                    }
                    new_detected_items.append((history_item, file_key))

            # Si de nouveaux fichiers sont détectés, on les enregistre tous immédiatement en base
            if new_detected_items:
                for item, _ in new_detected_items:
                    history.insert(0, item)
                    log_event("WATCHER", f"⚡ [DÉTECTÉ] Fichier '{item['filename']}' détecté pour la collection '{item['collection_name']}' -> Mis en traitement.")
                
                self.save_history(history)
                self.save_watchers(watchers)

                # Lancer immédiatement chaque fichier en tâche d'arrière-plan avec le sémaphore
                for item, f_key in new_detected_items:
                    asyncio.create_task(self._process_file_worker(item, f_key))

            return len(new_detected_items)

    async def _process_file_worker(self, history_item: Dict[str, Any], file_key: Tuple[str, int, int]):
        """Worker asynchrone qui exécute la conversion et l'indexation avec gestion de concurrence."""
        item_id = history_item["id"]
        filename = history_item["filename"]
        file_path = history_item["file_path"]
        collection_id = history_item["collection_id"]
        collection_name = history_item["collection_name"]

        try:
            async with self._semaphore:
                history_item["progress_step"] = "Conversion Markdown & Analyse LLM..."
                self._update_history_item(item_id, history_item)
                log_event("WATCHER", f"🔄 [CONVERSION] Traitement de '{filename}' pour '{collection_name}' (ID: {collection_id})...")

                # Étape 1 : Conversion en Markdown (.md) avec stockage hiérarchisé des images et analyse UML
                conv_result = await DocumentConverter.convert_to_markdown(
                    file_path=file_path,
                    filename=filename,
                    collection_name=collection_name
                )

                history_item["progress_step"] = "Indexation Albert API..."
                self._update_history_item(item_id, history_item)

                md_content = conv_result["markdown_content"]
                temp_id = str(uuid.uuid4())[:8]
                md_filename = f"{temp_id}_{Path(filename).stem}.md"
                converted_md_path = os.path.join(settings.CONVERTED_DIR, md_filename)

                os.makedirs(settings.CONVERTED_DIR, exist_ok=True)
                with open(converted_md_path, "w", encoding="utf-8") as f_md:
                    f_md.write(md_content)

                # Étape 2 : Envoi vers l'API Albert
                log_event("WATCHER", f"📤 [INDEXATION] Envoi de '{md_filename}' vers Albert API (Collection: {collection_id})...")
                ingest_result = await albert_client.upload_document(
                    collection_id=collection_id,
                    file_path=converted_md_path,
                    filename=f"{Path(filename).stem}.md"
                )

                albert_doc_id = str(ingest_result.get("id") or ingest_result.get("document_id") or "OK")

                # Étape 3 : Mise à jour des métadonnées locales documents_meta.json
                try:
                    docs_meta_file = os.path.join(settings.CONVERTED_DIR, "documents_meta.json")
                    docs = []
                    if os.path.exists(docs_meta_file):
                        with open(docs_meta_file, "r", encoding="utf-8") as f:
                            docs = json.load(f)
                    
                    new_doc_entry = {
                        "id": albert_doc_id,
                        "filename": md_filename,
                        "name": filename,
                        "collection_id": collection_id,
                        "original_format": Path(filename).suffix.lower(),
                        "size_chars": conv_result["char_count"],
                        "status": "indexed_in_albert",
                        "ingested_at": datetime.datetime.now().isoformat(),
                        "source": "auto_watcher"
                    }
                    docs.append(new_doc_entry)
                    with open(docs_meta_file, "w", encoding="utf-8") as f:
                        json.dump(docs, f, indent=2, ensure_ascii=False)
                except Exception as meta_err:
                    log_event("WATCHER", f"⚠️ Erreur mise à jour documents_meta.json: {meta_err}", level="WARNING")

                # Étape 4 : Finalisation statut 'completed'
                completed_time = datetime.datetime.now().isoformat()
                history_item.update({
                    "status": "completed",
                    "progress_step": "Terminé",
                    "albert_document_id": albert_doc_id,
                    "markdown_file": md_filename,
                    "completed_at": completed_time,
                    "char_count": conv_result["char_count"],
                    "pages_count": conv_result.get("pages_count", 1),
                    "tables_count": conv_result.get("tables_count", 0)
                })
                self._update_history_item(item_id, history_item)

                # Incrémenter les stats du watcher
                watchers = self.get_watchers()
                for w in watchers:
                    if w.get("id") == history_item.get("watcher_id"):
                        w["total_processed"] = w.get("total_processed", 0) + 1
                        break
                self.save_watchers(watchers)

                log_event("WATCHER", f"✅ [TRAITÉ] Document '{filename}' converti et indexé avec succès dans Albert API (Doc #{albert_doc_id})")

        except Exception as e:
            err_msg = str(e)
            log_event("WATCHER", f"❌ [ERREUR] Échec du traitement de '{filename}': {err_msg}", level="ERROR")
            history_item.update({
                "status": "error",
                "progress_step": "Erreur",
                "error_message": err_msg,
                "completed_at": datetime.datetime.now().isoformat()
            })
            self._update_history_item(item_id, history_item)

        finally:
            # Libérer la clé de tracking pour permettre une éventuelle relance future
            if file_key in self._active_file_keys:
                self._active_file_keys.discard(file_key)
            if item_id in self._active_task_ids:
                self._active_task_ids.discard(item_id)

    async def retry_history_item(self, item_id: str) -> bool:
        """Relance manuellement le traitement d'un fichier en erreur."""
        history = self.get_history(limit=500)
        target = None
        for h in history:
            if h.get("id") == item_id:
                target = h
                break
        if not target:
            return False

        file_path = target.get("file_path")
        if not file_path or not os.path.exists(file_path):
            target["status"] = "error"
            target["error_message"] = "Le fichier physique source n'existe plus sur le disque."
            self._update_history_item(item_id, target)
            return False

        target["status"] = "processing"
        target["progress_step"] = "Relance en cours..."
        target["error_message"] = None
        target["started_at"] = datetime.datetime.now().isoformat()
        self._update_history_item(item_id, target)

        stat = os.stat(file_path)
        file_key = (os.path.normcase(os.path.normpath(file_path)), int(stat.st_mtime), stat.st_size)
        self._active_file_keys.add(file_key)
        self._active_task_ids.add(item_id)

        # Relancer en tâche d'arrière-plan
        asyncio.create_task(self._process_file_worker(target, file_key))
        return True

# Instance singleton du WatcherService
watcher_service = WatcherService()

import os
import sys
import time
import asyncio
import shutil
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.services.watcher_service import watcher_service
from app.services.converter import DocumentConverter
from app.core.config import settings

TEST_WATCH_DIR = Path(__file__).resolve().parent / "test_multi_watch"

async def test_simultaneous_multi_file_detection():
    print("\n" + "="*70)
    print("🧪 TEST DE DETECTION SIMULTANEE MULTI-FICHIERS ET STATUT TEMPS REEL")
    print("="*70)

    # 1. Clean & setup test dir
    if TEST_WATCH_DIR.exists():
        shutil.rmtree(TEST_WATCH_DIR)
    TEST_WATCH_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Configure a test watcher
    watcher = watcher_service.create_or_update_watcher(
        collection_id="test_col_multi_99",
        collection_name="Collection Test Multi-Fichiers",
        folder_path=str(TEST_WATCH_DIR),
        filter_pattern="*.txt",
        enabled=True,
        recursive=False
    )
    watcher_id = watcher["id"]
    print(f"✅ Watcher créé avec succès: ID={watcher_id} pour le dossier {TEST_WATCH_DIR}")

    # 3. Create 3 files SIMULTANEOUSLY in the watched folder
    file_paths = []
    for i in range(1, 4):
        p = TEST_WATCH_DIR / f"test_doc_{i}.txt"
        p.write_text(f"# Document Test {i}\n\nCeci est le contenu textuel du document de test numéro {i} pour l'ingestion.", encoding="utf-8")
        file_paths.append(p)
    print(f"✅ 3 fichiers créés simultanément dans {TEST_WATCH_DIR}")

    # 4. Trigger scan_all_folders (this should execute in <100ms and register all 3 files as 'processing')
    t0 = time.time()
    detected_count = await watcher_service.scan_all_folders()
    scan_duration = time.time() - t0
    print(f"⚡ Scan terminé en {scan_duration*1000:.1f}ms : {detected_count} nouveau(x) fichier(s) détecté(s)")

    assert detected_count == 3, f"Attendu 3 fichiers détectés, obtenu {detected_count}"

    # 5. Check history immediately
    history = watcher_service.get_history(limit=50)
    test_items = [h for h in history if h.get("watcher_id") == watcher_id]
    print(f"📊 Items dans l'historique pour ce watcher : {len(test_items)}")
    for item in test_items:
        print(f"   - {item['filename']}: status='{item['status']}', step='{item['progress_step']}'")

    assert len(test_items) == 3, f"Tous les 3 fichiers doivent être dans l'historique! Trouvé: {len(test_items)}"
    for item in test_items:
        assert item["status"] == "processing", f"Le statut initial doit être 'processing', obtenu {item['status']}"

    # 6. Wait for the background workers to complete the conversion/processing
    print("\n⏳ Attente de la fin du traitement par les workers asynchrones...")
    max_wait = 15
    start_wait = time.time()
    while time.time() - start_wait < max_wait:
        await asyncio.sleep(0.5)
        history = watcher_service.get_history(limit=50)
        test_items = [h for h in history if h.get("watcher_id") == watcher_id]
        processing_remaining = sum(1 for h in test_items if h["status"] == "processing")
        if processing_remaining == 0:
            break

    print(f"🏁 Traitement terminé en {time.time() - start_wait:.1f}s")
    for item in test_items:
        print(f"   - {item['filename']}: status='{item['status']}', step='{item['progress_step']}', doc_id={item.get('albert_document_id')}")

    # 7. Verify all completed or processed
    assert all(item["status"] in ("completed", "error") for item in test_items), "Tous les fichiers doivent être terminés"

    # 8. Clean up watcher
    watcher_service.delete_watcher(watcher_id)
    if TEST_WATCH_DIR.exists():
        shutil.rmtree(TEST_WATCH_DIR)
    print("\n✅ Nettoyage terminé. Tous les tests sont passés avec succès !")

if __name__ == "__main__":
    asyncio.run(test_simultaneous_multi_file_detection())

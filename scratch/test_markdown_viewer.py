import sys
import json
from pathlib import Path
from fastapi.testclient import TestClient

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.main import app

def test_markdown_viewer_endpoints():
    client = TestClient(app)

    print("\n" + "="*70)
    print("🧪 TEST DES ENDPOINTS DE VISUALISATION MARKDOWN (.MD)")
    print("="*70)

    # 1. Tester la liste des watchers pour récupérer un item d'historique
    res_live = client.get("/api/v1/watchers/live")
    assert res_live.status_code == 200, f"Erreur live endpoint: {res_live.status_code}"
    live_data = res_live.json()
    history = live_data.get("history", [])

    print(f"📊 {len(history)} document(s) trouvés dans l'historique")
    
    # Trouver un item complété
    completed_item = next((h for h in history if h.get("status") == "completed" and h.get("markdown_file")), None)
    if not completed_item and history:
        completed_item = history[0]

    if completed_item:
        item_id = completed_item["id"]
        print(f"🔍 Test de récupération Markdown pour l'item #{item_id} ('{completed_item['filename']}')...")
        
        res_md = client.get(f"/api/v1/watchers/history/{item_id}/markdown")
        print(f"   Status code: {res_md.status_code}")
        assert res_md.status_code == 200, f"Erreur récupération markdown watcher: {res_md.text}"
        data = res_md.json()
        print(f"   ✅ Fichier: {data.get('markdown_file')}")
        print(f"   ✅ Caractères: {data.get('char_count')}")
        print(f"   ✅ Aperçu contenu (100 premiers caractères): {data.get('markdown_content')[:100]}...")
        assert "markdown_content" in data
        assert len(data["markdown_content"]) > 0

    # 2. Tester l'endpoint /documents/{document_id}/markdown
    if completed_item and completed_item.get("albert_document_id"):
        doc_id = completed_item["albert_document_id"]
        print(f"\n🔍 Test de récupération Markdown via /documents/{doc_id}/markdown...")
        res_doc_md = client.get(f"/api/v1/documents/{doc_id}/markdown?filename={completed_item.get('filename')}")
        print(f"   Status code: {res_doc_md.status_code}")
        assert res_doc_md.status_code == 200, f"Erreur récupération document markdown: {res_doc_md.text}"
        data_doc = res_doc_md.json()
        print(f"   ✅ Document ID: {data_doc.get('document_id')}")
        print(f"   ✅ Nom: {data_doc.get('filename')}")
        print(f"   ✅ Caractères: {data_doc.get('char_count')}")
        assert "markdown_content" in data_doc

    print("\n✅ TOUS LES TESTS DE VISUALISATION MARKDOWN SONT PASSÉS AVEC SUCCÈS !")

if __name__ == "__main__":
    test_markdown_viewer_endpoints()

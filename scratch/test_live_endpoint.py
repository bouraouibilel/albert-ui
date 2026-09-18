import sys
from pathlib import Path
from fastapi.testclient import TestClient

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.main import app

def test_watchers_live_endpoint():
    client = TestClient(app)
    response = client.get("/api/v1/watchers/live")
    assert response.status_code == 200
    data = response.json()
    print("✅ Live endpoint response status:", response.status_code)
    print("📊 Keys returned:", list(data.keys()))
    print("📈 Stats:", data.get("stats"))
    print("📁 Watchers count:", len(data.get("watchers", [])))
    print("📜 History items count:", len(data.get("history", [])))
    assert "watchers" in data
    assert "stats" in data
    assert "history" in data
    assert "processing_count" in data["stats"]
    assert "completed_count" in data["stats"]
    assert "error_count" in data["stats"]

if __name__ == "__main__":
    test_watchers_live_endpoint()

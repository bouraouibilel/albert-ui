# Albert RAG Admin - Interface d'Administration & Studio de Conversion Markdown

Interface d'administration pour la gestion des collections et documents destinés au RAG d'**Albert API** (DINUM / Etalab) et consommables par **Open WebUI**.

---

## 🎯 Fonctionnalités Clés

1. **Étape Cruciale : Studio de Conversion Vers Markdown (`.md`)**
   - Importation multi-formats : PDF (texte & scanné avec tables), Word (`.docx`), Excel (`.xlsx`), HTML, Texte (`.txt`).
   - Pré-conversion universelle en Markdown propre avec métadonnées YAML Front-Matter.
   - Éditeur côte à côte avec aperçu du rendu en temps réel avant indexation.

2. **Gestionnaire de Collections Albert API**
   - Création, consultation et suppression de collections dans Albert API (`/v1/collections`).
   - Ingestion des fichiers Markdown convertis dans l'index vectoriel Albert API (`/v1/documents`).

3. **Sandbox RAG Admin & Reranking**
   - Évaluation du réordonnancement sémantique via l'endpoint `/v1/rerank` d'Albert API.
   - Simulation de requêtes RAG et contrôle des citations avant déploiement dans Open WebUI.

---

## 🚀 Démarrage Rapide

### 1. Démarrer le Serveur Backend & l'UI Web

Ouvrez un terminal dans le dossier `d:\work\sample\Albert\backend` et lancez :

```bash
cd backend
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Accéder à l'Interface d'Administration

Ouvrez votre navigateur web sur :
👉 **`http://localhost:8000`** ou **`http://localhost:8000/admin`**

### 3. Configuration de la Clé Albert API (Optionnel)

Définissez la variable d'environnement `ALBERT_API_KEY` dans votre système ou dans un fichier `.env` dans `backend/` :

```env
ALBERT_API_BASE_URL=https://albert.api.etalab.gouv.fr/v1
ALBERT_API_KEY=votre_cle_api_albert
```

---

## 🔌 Intégration avec Open WebUI

Les collections et documents pré-convertis en `.md` et indexés via cette interface dans Albert API sont directement accessibles dans **Open WebUI** en configurant le fournisseur RAG / OpenAI vers votre instance Albert API (`https://albert.api.etalab.gouv.fr/v1`).
# albert-ui
# albert-ui

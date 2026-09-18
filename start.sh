#!/bin/bash

echo "========================================================"
echo "  Albert RAG Admin - Studio Markdown & Albert API Server"
echo "========================================================"

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR/backend"

# Libérer le port 8000 sur AlmaLinux si un ancien processus uvicorn/python tournait en arrière-plan
echo "Vérification des processus sur le port 8000..."
fuser -k 8000/tcp 2>/dev/null || pkill -f "uvicorn app.main:app" 2>/dev/null || true

if [ ! -d ".venv" ]; then
    echo "Création de l'environnement virtuel Python..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Démarrage du serveur Uvicorn sur 0.0.0.0:8000..."
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

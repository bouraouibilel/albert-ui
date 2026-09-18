#!/bin/bash

echo "========================================================"
echo "  Albert RAG Admin - Studio Markdown & Albert API Server"
echo "========================================================"

cd "$(dirname "$0")/backend"
python -m venv .venv

source .venv/bin/activate

pip install -r requirements.txt

.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

read -p "Appuyez sur Entrée pour continuer..."

#!/usr/bin/env bash
# Starts API (:8000) and Streamlit UI (:8501). Synthetic data only. Audit log: var/audit.jsonl (hash-chained).
cd "$(dirname "$0")"; . .venv/bin/activate
python hooks/session_start.py || exit 1
mkdir -p var
uvicorn app.api:create_app --factory --host 127.0.0.1 --port 8000 > var/api.log 2>&1 &
API_URL=http://127.0.0.1:8000 streamlit run app/ui.py --server.port 8501 --server.address 127.0.0.1 --server.headless true > var/ui.log 2>&1 &
echo "UI: http://localhost:8501   API docs: http://localhost:8000/docs   metrics: http://localhost:8000/metrics"

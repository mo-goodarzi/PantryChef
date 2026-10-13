#!/usr/bin/env sh
# Hugging Face Space entrypoint: data, then the API (inside the container only), then the
# UI on the Space's public port. Secrets (OPENAI_API_KEY, YOUTUBE_API_KEY, HF_TOKEN) and
# variables (DATA_REPO, DAILY_BUDGET_USD, MAX_TURNS_PER_SESSION) come from the Space settings.
set -e
cd /app
python -m pantry_chef.deploy fetch --dest data/processed

uvicorn pantry_chef.api.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!
# Wait for the API (it loads the embedding model and opens the databases).
until python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" 2>/dev/null; do
  kill -0 "$API_PID" 2>/dev/null || { echo "API failed to start"; exit 1; }
  sleep 2
done

export PANTRY_CHEF_API_URL=http://127.0.0.1:8000
exec streamlit run ui/streamlit_app.py --server.address=0.0.0.0 --server.port=7860 \
  --server.headless=true

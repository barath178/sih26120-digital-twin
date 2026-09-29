#!/usr/bin/env bash
# One-command local demo (Linux / macOS). Serves UI + API on http://127.0.0.1:8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  python3.12 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
fi
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
npm run build
cd "$ROOT/backend"
.venv/bin/python -m app.train
echo "Open http://127.0.0.1:8000"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

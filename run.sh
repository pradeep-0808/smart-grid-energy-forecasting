#!/usr/bin/env bash
# Start the Smart Grid platform (dashboard + API) on port 8000.
set -e
cd "$(dirname "$0")"
python3 -c "import fastapi, uvicorn, sklearn, pandas, reportlab" 2>/dev/null || pip install -r requirements.txt
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"

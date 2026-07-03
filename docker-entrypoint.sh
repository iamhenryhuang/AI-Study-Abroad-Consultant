#!/bin/sh
set -e

echo "[entrypoint] 啟動 API server..."
exec uvicorn backend.api:app --host 0.0.0.0 --port 8000

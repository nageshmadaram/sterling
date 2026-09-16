#!/usr/bin/env bash
# Start the Sterling backend for the frozen Snapback prospective runtime.
#
# Production launch: a single uvicorn worker, no --reload. One worker is required
# because the health heartbeats, alert de-duplication and ops-scheduler state are
# in-process; multi-process workers would each keep their own copy and would need
# validated cross-process locking first.
#
# Only STERLING_* variables are exported here. Every other setting is read from
# backend/.env by pydantic-settings, which parses values (e.g. CORS_ORIGINS) that a
# plain shell `source` would mangle.
set -euo pipefail

cd "$(dirname "$0")"

while IFS='=' read -r key value; do
  case "$key" in
    STERLING_*) export "$key=$value" ;;
  esac
done < <(grep -E '^STERLING_[A-Z_]+=' .env)

mkdir -p logs

exec ./.venv/bin/uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1

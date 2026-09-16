#!/usr/bin/env bash
# Start the Sterling backend with the Snapback prospective evidence environment.
# Only STERLING_* variables are exported here; every other setting is read from
# backend/.env by pydantic-settings, which parses values (e.g. CORS_ORIGINS) that
# a plain shell `source` would mangle.
set -euo pipefail

cd "$(dirname "$0")"

while IFS='=' read -r key value; do
  case "$key" in
    STERLING_*) export "$key=$value" ;;
  esac
done < <(grep -E '^STERLING_[A-Z_]+=' .env)

mkdir -p logs
exec ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000

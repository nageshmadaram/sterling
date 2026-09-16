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

# Loopback by default. Binding every interface on a home network exposes an
# authenticated broker session to whatever else is on the Wi-Fi; opening it is a
# deliberate act, so it takes STERLING_BIND_HOST.
exec ./.venv/bin/uvicorn main:app \
  --host "${STERLING_BIND_HOST:-127.0.0.1}" \
  --port 8000 \
  --workers 1

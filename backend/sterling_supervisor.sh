#!/usr/bin/env bash
# Liveness supervisor for the Sterling backend.
#
# Starts the backend and probes a cheap endpoint every 60 seconds. A process that
# merely exists is not healthy: the previous outage had a live worker that answered
# nothing. Three consecutive connection/timeout failures restart the backend.
set -uo pipefail

cd "$(dirname "$0")"

PROBE_URL="${STERLING_PROBE_URL:-http://127.0.0.1:8000/docs}"
PROBE_INTERVAL="${STERLING_PROBE_INTERVAL:-60}"
PROBE_TIMEOUT="${STERLING_PROBE_TIMEOUT:-15}"
MAX_FAILURES="${STERLING_PROBE_MAX_FAILURES:-3}"
STARTUP_GRACE="${STERLING_STARTUP_GRACE:-120}"

mkdir -p logs
LOG=logs/supervisor.log

log() { echo "$(date -Is) [supervisor] $*" | tee -a "$LOG"; }

BACKEND_PID=""

start_backend() {
  ./run_backend.sh >> logs/backend.log 2>&1 &
  BACKEND_PID=$!
  log "started backend pid=$BACKEND_PID; grace ${STARTUP_GRACE}s"
  sleep "$STARTUP_GRACE"
}

stop_backend() {
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "$BACKEND_PID" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$BACKEND_PID" 2>/dev/null || true
  fi
  pkill -9 -f "uvicorn main:app" 2>/dev/null || true
  sleep 2
}

trap 'log "supervisor stopping"; stop_backend; exit 0' INT TERM

start_backend

failures=0
while true; do
  if curl -fsS -m "$PROBE_TIMEOUT" -o /dev/null "$PROBE_URL"; then
    if (( failures > 0 )); then
      log "probe recovered after ${failures} failure(s)"
    fi
    failures=0
  else
    failures=$((failures + 1))
    log "probe FAILED (${failures}/${MAX_FAILURES}) url=$PROBE_URL"
    if (( failures >= MAX_FAILURES )); then
      log "restarting backend after ${failures} consecutive probe failures"
      stop_backend
      start_backend
      failures=0
    fi
  fi
  sleep "$PROBE_INTERVAL"
done

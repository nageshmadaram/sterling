#!/usr/bin/env bash
# Refuse to release with a credential committed to the repository.
#
# Scans tracked files only: an untracked local .env is fine, a committed one is
# not. Patterns are deliberately narrow so the gate stays actionable rather than
# being routinely overridden.
set -uo pipefail

PATTERNS=(
  'kite[_-]?api[_-]?secret[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9]{8,}'
  'access[_-]?token[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9]{20,}'
  'TELEGRAM_BOT_TOKEN[[:space:]]*[=:][[:space:]]*["'"'"']?[0-9]{6,}:[A-Za-z0-9_-]{20,}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'AKIA[0-9A-Z]{16}'
)

status=0
while IFS= read -r file; do
  case "$file" in
    *snapback_secret_scan.sh) continue ;;
    *.png|*.jpg|*.jpeg|*.gif|*.pdf|*.zip|*.db) continue ;;
  esac
  for pattern in "${PATTERNS[@]}"; do
    if grep -HnaE "$pattern" -- "$file" 2>/dev/null; then
      echo "SECRET PATTERN in $file" >&2
      status=1
    fi
  done
done < <(git ls-files)

if [ "$status" -ne 0 ]; then
  echo "Release blocked: credential-shaped content is committed." >&2
  exit 1
fi
echo "No committed credentials found."

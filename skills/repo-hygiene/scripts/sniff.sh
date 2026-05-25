#!/usr/bin/env bash
# sniff.sh — fetch a git-sniff scorecard as JSON for a GitHub repository.
#
# Usage:   sniff.sh <owner>/<repo> [port]
# Output:  the raw JSON scorecard from the git-sniff microservice on stdout.
# Exit:    0 success; 2 bad args; 3 server unavailable; 4 sniff request failed.
#
# Behavior: reuses a running git-sniff server on the target port; if none is
# running, starts one in the background and waits for it to become healthy.
# Honors GITHUB_TOKEN from the environment to lift GitHub's 60/hr unauth cap.

set -euo pipefail

REPO="${1:-}"
PORT="${2:-8000}"
BASE="http://127.0.0.1:${PORT}"

if [[ -z "$REPO" || "$REPO" != */* ]]; then
  echo "error: expected argument '<owner>/<repo>' (e.g. NACLab/ngc-learn)" >&2
  exit 2
fi

server_healthy() {
  curl -fsS -o /dev/null --max-time 3 "${BASE}/docs" 2>/dev/null
}

if ! server_healthy; then
  if ! command -v git-sniff >/dev/null 2>&1; then
    echo "error: git-sniff not installed and no server on ${BASE} (pip install -e . in the git-sniff repo)" >&2
    exit 3
  fi
  # Start the microservice detached; its own process keeps running after this script exits.
  nohup git-sniff --server --port "$PORT" >/tmp/git-sniff-server.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 0.5
    server_healthy && break
  done
  if ! server_healthy; then
    echo "error: git-sniff server failed to start on ${BASE} (see /tmp/git-sniff-server.log)" >&2
    exit 3
  fi
fi

# Scorecard requests can take up to ~15s on cold GitHub stats compilation.
HTTP_BODY="$(curl -sS --max-time 40 -w $'\n%{http_code}' "${BASE}/sniff?repo=${REPO}")"
CODE="${HTTP_BODY##*$'\n'}"
JSON="${HTTP_BODY%$'\n'*}"

if [[ "$CODE" != "200" ]]; then
  echo "error: sniff request failed (HTTP ${CODE}): ${JSON}" >&2
  exit 4
fi

printf '%s\n' "$JSON"

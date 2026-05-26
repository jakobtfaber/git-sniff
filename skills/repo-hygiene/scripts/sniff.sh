#!/usr/bin/env bash
# sniff.sh — fetch a git-sniff scorecard as JSON for a GitHub repository.
#
# Usage:   sniff.sh <owner>/<repo>
# Output:  the JSON scorecard on stdout.
# Exit:    0 success; 2 bad args; 3 git-sniff not installed; 4 sniff request failed.
#
# Behavior: invokes the git-sniff CLI in JSON mode (no server, no daemon, no
# open port). The CLI resolves a GitHub token from the macOS Keychain (service
# 'Agents', account 'github-pat') or the GITHUB_PERSONAL_ACCESS_TOKEN env var to
# lift GitHub's 60/hr unauthenticated cap.

set -euo pipefail

REPO="${1:-}"

if [[ -z "$REPO" || "$REPO" != */* ]]; then
  echo "error: expected argument '<owner>/<repo>' (e.g. NACLab/ngc-learn)" >&2
  exit 2
fi

if ! command -v git-sniff >/dev/null 2>&1; then
  echo "error: git-sniff not installed (pip install -e . in the git-sniff repo)" >&2
  exit 3
fi

# git-sniff --json prints the scorecard JSON on success (exit 0), or
# {"error": "..."} and exit 1 on failure.
if ! OUT="$(git-sniff --json "$REPO")"; then
  echo "error: sniff request failed: ${OUT}" >&2
  exit 4
fi

printf '%s\n' "$OUT"

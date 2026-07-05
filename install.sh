#!/usr/bin/env bash
# Claude Data Skills — one-line installer for the `data-team` plugin.
#
#   curl -fsSL https://raw.githubusercontent.com/t4tarzan/claude-data-skills/main/install.sh | bash
#
# Re-run any time to pull the latest version (idempotent).
set -euo pipefail

REPO="t4tarzan/claude-data-skills"   # marketplace source (the GitHub repo)
MARKET="claude-data-skills"          # marketplace name (the `name` field in marketplace.json)
PLUGIN="data-team"                   # plugin name

say() { printf '\033[1;35m%s\033[0m\n' "$*"; }

if command -v claude >/dev/null 2>&1; then
  say "→ adding marketplace $REPO"
  # add if new, else refresh to latest (both paths land on newest)
  claude plugin marketplace add "$REPO" 2>/dev/null || claude plugin marketplace update "$MARKET" || true
  say "→ installing $PLUGIN@$MARKET"
  claude plugin install "$PLUGIN@$MARKET"
  say "✓ done — invoke it in Claude Code:  /data-team:run"
  echo
  echo "Keep it current automatically:  /plugin  →  Marketplaces  →  $MARKET  →  Enable auto-update"
  echo "Or update on demand:            /plugin marketplace update $MARKET"
else
  cat <<EOF
The 'claude' CLI was not found on your PATH.
Install Claude Code (https://claude.com/claude-code), then run these two lines inside a session:

  /plugin marketplace add $REPO
  /plugin install $PLUGIN@$MARKET

…and invoke it with:  /data-team:run
EOF
fi

#!/usr/bin/env bash
set -euo pipefail

PORT="${CHROME_CDP_PORT:-9223}"
FLOW_URL="${FLOW_URL:-https://labs.google/fx/tools/flow}"
# Windows host IP from WSL:
HOST="${CHROME_CDP_HOST:-$(awk '/^nameserver /{print $2; exit}' /etc/resolv.conf)}"

check() { curl -s --max-time 1 "http://$HOST:$PORT/json/version" | grep -q '"Browser"'; }

if check; then
  echo "✅ CDP alive at $HOST:$PORT"
  exit 0
fi

echo "♻️  Restarting Edge with CDP…"
# Launch PS1 that you already have:
#   C:\Users\bgrut\launch_edge_cdp.ps1
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -File \
  C:\\Users\\bgrut\\launch_edge_cdp.ps1 -Port $PORT -Address 0.0.0.0 -Url "$FLOW_URL"

# Wait up to 15s
for i in {1..30}; do
  if check; then echo "✅ CDP ready at $HOST:$PORT"; exit 0; fi
  sleep 0.5
done

echo "❌ Edge CDP not reachable at $HOST:$PORT"
exit 1

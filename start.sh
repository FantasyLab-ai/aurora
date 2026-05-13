#!/usr/bin/env bash
# Aurora Studio — macOS / Linux launcher.
#
# Usage (from repo root):
#   ./start.sh                    # standard launch on port 8000
#   AURORA_PORT=8080 ./start.sh
#   AURORA_SKIP_INSTALL=1 ./start.sh   # skip dependency check

set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# 1. venv
if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
    echo "[aurora] using venv: $PY"
elif [[ -x ".venv/Scripts/python.exe" ]]; then  # mingw / git-bash on windows
    PY=".venv/Scripts/python.exe"
    echo "[aurora] using venv: $PY"
else
    PY="${PYTHON:-python3}"
    echo "[aurora] no .venv found — using $PY"
fi

# 2. deps
if [[ -z "${AURORA_SKIP_INSTALL:-}" ]]; then
    echo "[aurora] checking dependencies..."
    "$PY" -m pip install --quiet -r requirements.txt || \
        echo "[aurora] WARN: pip install returned non-zero; continuing anyway."
fi

# 3. env
export AURORA_HOST="${AURORA_HOST:-127.0.0.1}"
export AURORA_PORT="${AURORA_PORT:-8000}"

# 4. browser (best-effort)
URL="http://$AURORA_HOST:$AURORA_PORT"
if [[ -z "${AURORA_NO_BROWSER:-}" ]]; then
    (sleep 2 && {
        if command -v open >/dev/null;     then open "$URL";
        elif command -v xdg-open >/dev/null; then xdg-open "$URL";
        fi
    }) &
fi

# 5. server
echo "[aurora] starting Flask on $URL"
exec "$PY" studio_api.py

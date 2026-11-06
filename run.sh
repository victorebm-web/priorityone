#!/usr/bin/env bash
# PriorityOne: one-command local start.
# Creates a venv, installs requirements, seeds the DB, scores the last 8 weeks, starts the server.
set -euo pipefail
cd "$(dirname "$0")"

# Prefer the newest Python available. Target is 3.11+; the code also runs on 3.9+.
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then echo "No python3 found on PATH." >&2; exit 1; fi
"$PY" -c 'import sys; v=sys.version_info; sys.exit(0 if v>=(3,9) else 1)' \
  || { echo "Python 3.9+ required (3.11+ recommended)." >&2; exit 1; }
"$PY" -c 'import sys; v=sys.version_info; v<(3,11) and print(f"Note: running on Python {v.major}.{v.minor}; 3.11+ is recommended.")'

if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

python seed.py
python jobs.py --last-weeks 8 --demo-feedback

PORT="${PORT:-8000}"
echo "PriorityOne running at http://127.0.0.1:${PORT}"
exec uvicorn main:app --host 127.0.0.1 --port "$PORT"

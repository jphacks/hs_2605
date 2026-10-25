#!/bin/bash
# IBUKI: start everything with one command. Double-click this file in Finder, or run:
#   ./ibuki.command                                  the live board
#   ./ibuki.command --replay recordings/<file>.csv   a recording instead (any server.py option works)
# The first start installs the Python packages and builds the dashboard, so it takes a minute.
# The dashboard opens at http://localhost:8000 (choose LIVE). Ctrl+C or closing the window stops it.
set -e
cd "$(dirname "$0")"

# Python: the team's ../env if it exists, otherwise a .venv in this folder
if [ -x ../env/bin/python ]; then
  PY=../env/bin/python
else
  [ -x .venv/bin/python ] || python3 -m venv .venv
  PY=.venv/bin/python
fi
if ! "$PY" -c "import fastapi, uvicorn, websockets, numpy, scipy, serial, dotenv, requests" 2>/dev/null; then
  echo "Installing the Python packages ..."
  "$PY" -m pip install -q -r requirements.txt
fi

# Dashboard: build it the first time and whenever web/ changes; the server serves it on port 8000
if [ ! -f web/dist/index.html ] || [ -n "$(find web/src web/index.html web/package.json -newer web/dist/index.html -print -quit)" ]; then
  if ! command -v npm >/dev/null; then
    echo "The dashboard needs Node.js: install it from https://nodejs.org and start again."
    exit 1
  fi
  echo "Building the dashboard ..."
  (cd web && { [ -d node_modules ] || npm install --silent; } && npm run build --silent)
fi

# Open the browser as soon as the server answers
(for _ in $(seq 1 40); do
   curl -s -o /dev/null http://localhost:8000/api/health && open http://localhost:8000 && break
   sleep 0.5
 done) &

exec "$PY" pc/ibuki/server.py "$@"

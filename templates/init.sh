#!/usr/bin/env bash
# init.sh - idempotent environment setup for the stack declared in services.yaml.
#
# A real Factory Mission ships an init.sh that VERIFIES a precondition and
# fails with an actionable message rather than installing silently. This script
# does not install a database, an interpreter, or a dependency: it checks that
# each is present and at the version the stack needs, and tells the human
# exactly what to do when one is not. Run it once before `up`; run it again
# after any checkout and it will not redo work that is already done.
#
# Edit ROOT to point at the repository root (the parent of .relay).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[init] 1/5 checking the database is reachable at localhost:5432"
if ! pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
  echo "[init]   postgres is not reachable on localhost:5432." >&2
  echo "[init]   start it (e.g. 'docker compose up -d postgres') and re-run init.sh." >&2
  exit 1
fi
echo "[init]   postgres is reachable."

echo "[init] 2/5 checking the interpreter version"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[init]   python3 is not on PATH. Install Python 3.11+ and re-run." >&2
  exit 1
fi
PY_VERSION="$(python3 -c 'import sys; print(sys.version_info[:2])')"
if [ "$PY_VERSION" != "(3, 11)" ] && [ "$PY_VERSION" != "(3, 12)" ]; then
  echo "[init]   python3 is $PY_VERSION; this stack needs 3.11 or 3.12." >&2
  exit 1
fi
echo "[init]   python3 is $PY_VERSION."

echo "[init] 3/5 checking backend dependencies are installed"
if [ ! -d "$ROOT/backend/.venv" ]; then
  echo "[init]   no virtualenv at $ROOT/backend/.venv." >&2
  echo "[init]   run 'python3 -m venv $ROOT/backend/.venv && $ROOT/backend/.venv/bin/pip install -r $ROOT/backend/requirements.txt'." >&2
  exit 1
fi
if ! "$ROOT/backend/.venv/bin/python" -c "import api" >/dev/null 2>&1; then
  echo "[init]   the 'api' package is not importable in the backend venv." >&2
  echo "[init]   run '$ROOT/backend/.venv/bin/pip install -r $ROOT/backend/requirements.txt'." >&2
  exit 1
fi
echo "[init]   backend dependencies are present."

echo "[init] 4/5 checking migrations are at head"
HEAD="$("$ROOT/backend/.venv/bin/python" -m api migrations current 2>/dev/null || true)"
LATEST="$("$ROOT/backend/.venv/bin/python" -m api migrations latest 2>/dev/null || true)"
if [ -z "$HEAD" ] || [ "$HEAD" != "$LATEST" ]; then
  echo "[init]   migrations are at '$HEAD', latest is '$LATEST'." >&2
  echo "[init]   run '$ROOT/backend/.venv/bin/python -m api migrations upgrade'." >&2
  exit 1
fi
echo "[init]   migrations are at head ($HEAD)."

echo "[init] 5/5 checking frontend dependencies are installed"
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "[init]   no node_modules at $ROOT/frontend/node_modules." >&2
  echo "[init]   run 'cd $ROOT/frontend && npm install'." >&2
  exit 1
fi
echo "[init]   frontend dependencies are present."

echo "[init] the stack is ready. Start it with:"
echo "[init]   python3 scripts/relay_services.py up --relay-dir $ROOT/.relay"

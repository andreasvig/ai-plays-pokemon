#!/usr/bin/env bash
#
# Bootstrap a fresh clone of ai-plays-pokemon.
#
# Idempotent — safe to re-run. Handles the project-local setup an AI agent (or a
# new human) needs after `git clone`:
#   1. verifies system prerequisites (python 3.11+, node/npm, and — for actually
#      RUNNING — mGBA + tesseract + a ROM), with install hints,
#   2. creates the Python venv and does the editable install (deps come from
#      pyproject.toml — there is no requirements.txt),
#   3. builds the control-center web UI (src/dashboard/web → dist/, gitignored),
#   4. seeds .env from .env.example.
#
# System packages (mGBA, tesseract) and the copyrighted ROM are NOT installed by
# this script — it only checks for them and tells you what's missing. Run from
# anywhere; it cd's to the repo root itself.
#
# Usage:
#   scripts/setup.sh                 # full setup
#   scripts/setup.sh --skip-frontend # Python only (headless `pokemon run`)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SKIP_FRONTEND=0
for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

# --- 1. Prerequisites --------------------------------------------------------
bold "Checking prerequisites"

# Python 3.11+ (hard requirement — the install can't proceed without it).
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 not found. Install Python 3.11 or newer."
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 11) else 0)')"
[ "$PY_OK" = "1" ] || die "Python $PY_VER found, but 3.11+ is required."
ok "python3 $PY_VER"

# Node/npm (needed only to build the control-center UI).
if [ "$SKIP_FRONTEND" = "0" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    die "npm not found, needed to build the web UI. Install Node.js 18+ (https://nodejs.org), or re-run with --skip-frontend for the headless CLI only."
  fi
  ok "node $(node --version 2>/dev/null), npm $(npm --version 2>/dev/null)"
fi

# Runtime-only deps — warn, don't fail (the install completes without them; they
# are needed when you actually launch a run).
command -v tesseract >/dev/null 2>&1 \
  && ok "tesseract $(tesseract --version 2>&1 | head -1 | awk '{print $2}')" \
  || warn "tesseract not found (needed at runtime for OCR). macOS: brew install tesseract · Debian: sudo apt install tesseract-ocr"

if command -v mgba-qt >/dev/null 2>&1 || command -v mgba >/dev/null 2>&1 \
   || [ -x "/Applications/mGBA.app/Contents/MacOS/mGBA" ]; then
  ok "mGBA found"
else
  warn "mGBA not found (needed at runtime). macOS: brew install mgba · Debian: sudo apt install mgba-qt · or https://mgba.io"
fi

if ls roms/*.gba >/dev/null 2>&1; then
  ok "ROM present in roms/"
else
  warn "No .gba ROM in roms/ (gitignored). Drop a Pokemon FireRed (USA, Europe, Rev 1) ROM you legally own there — see README 'Prerequisites'."
fi

# --- 2. Python environment ---------------------------------------------------
bold "Python environment"
if [ ! -d venv ]; then
  python3 -m venv venv
  ok "created venv/"
else
  ok "venv/ already exists"
fi
# Use the venv's interpreter directly — no need to `source activate`. Some venvs
# (notably uv-created ones) ship without pip; ensurepip bootstraps it.
./venv/bin/python -m pip --version >/dev/null 2>&1 || ./venv/bin/python -m ensurepip --upgrade >/dev/null
./venv/bin/python -m pip install --quiet --upgrade pip
./venv/bin/python -m pip install --quiet -e .
ok "installed dependencies + the 'pokemon' CLI (editable, from pyproject.toml)"

# --- 3. Web UI ---------------------------------------------------------------
if [ "$SKIP_FRONTEND" = "0" ]; then
  bold "Control-center web UI"
  ( cd src/dashboard/web && npm install --silent && npm run build >/dev/null )
  ok "built src/dashboard/web/dist/"
else
  bold "Control-center web UI"
  warn "skipped (--skip-frontend). 'pokemon app' will report 'SPA not built' until you run: cd src/dashboard/web && npm install && npm run build"
fi

# --- 4. .env -----------------------------------------------------------------
bold "Environment file"
if [ ! -f .env ]; then
  cp .env.example .env
  warn "created .env from .env.example — edit it and set OPENROUTER_API_KEY (https://openrouter.ai/keys)"
else
  ok ".env already exists"
fi

# --- Done --------------------------------------------------------------------
bold "Setup complete"
echo "Next steps:"
echo "  1. Set OPENROUTER_API_KEY in .env"
echo "  2. Ensure a legally-obtained FireRed ROM is in roms/ (see README)"
echo "  3. Activate the venv:  source venv/bin/activate"
echo "  4. Start the control center:  pokemon app   (then load lua/socketserver-1.lua in mGBA's Scripting window)"
echo "     …or a headless run:  pokemon run --model \"gemini-3.5-flash(medium)\""

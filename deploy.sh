#!/bin/bash
# deploy.sh — Setup and deploy script for PythonAnywhere
# Usage (first time): bash deploy.sh
# Usage (subsequent deploys): bash deploy.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

echo "=== Fantasy Tool — PythonAnywhere Deploy ==="
echo "Project dir : $PROJECT_DIR"
echo "Virtualenv  : $VENV_DIR"
echo ""

# ── 1. Virtualenv ──────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/5] Creating virtual environment..."
    python3.10 -m venv "$VENV_DIR"
else
    echo "[1/5] Virtual environment already exists."
fi

source "$VENV_DIR/bin/activate"

# ── 2. Dependencies ────────────────────────────────────────────────────────────
echo "[2/5] Installing/upgrading dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$PROJECT_DIR/requirements.txt"

# ── 3. Static files ────────────────────────────────────────────────────────────
echo "[3/5] Collecting static files..."
cd "$PROJECT_DIR"
python manage.py collectstatic --noinput

# ── 4. Database migrations ─────────────────────────────────────────────────────
echo "[4/5] Running database migrations..."
python manage.py migrate

# ── 5. Done ────────────────────────────────────────────────────────────────────
echo ""
echo "[5/5] Done!"
echo ""
echo "Checklist:"
echo "  [ ] .env is present at $PROJECT_DIR/.env with production values"
echo "  [ ] WSGI file is configured (see pythonanywhere_wsgi.py in this dir)"
echo "  [ ] Web app is reloaded in PythonAnywhere dashboard"

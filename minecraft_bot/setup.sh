#!/usr/bin/env bash
# ===========================================================================
# setup.sh — One-shot setup for the Minecraft Automation Bot (pure Python)
# ===========================================================================
# No Node.js / npm required.  Just Python 3.9+ and pip.
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║     Minecraft Bot — Pure Python Setup Script     ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Python check ────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found in PATH. Install Python 3.9+ first."
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python version: $PY_VERSION"

# ── Install Python dependencies ─────────────────────────────────────────────
echo ""
echo "Installing Python dependencies…"
pip install --upgrade pip --quiet
pip install -r requirements.txt

echo ""
echo "✓ Python dependencies installed"

# ── Create .env from example if missing ─────────────────────────────────────
if [[ ! -f .env && -f .env.example ]]; then
    cp .env.example .env
    echo "✓ Created .env from .env.example — edit it with your settings"
else
    echo "✓ .env already exists"
fi

# ── Check ip.txt ─────────────────────────────────────────────────────────────
if [[ ! -s ip.txt ]]; then
    echo ""
    echo "⚠  ip.txt is empty. Add your server IP/hostname before running:"
    echo "   echo 'your.server.ip' > ip.txt"
fi

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║              Setup complete!                     ║"
echo "║  Run with:  python3 main.py                      ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

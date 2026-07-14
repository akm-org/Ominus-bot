#!/usr/bin/env bash
# =============================================================================
# setup.sh — One-shot setup script for the Minecraft Automation Bot
# =============================================================================
# Run once before first launch:
#   chmod +x setup.sh && ./setup.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Minecraft Bot — Setup                          ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# 1. Check Python version
# ---------------------------------------------------------------------------
echo "▶ Checking Python 3.13+…"
if ! python3 --version 2>&1 | grep -qE "3\.(1[3-9]|[2-9][0-9])"; then
    echo "  ❌  Python 3.13 or newer is required."
    echo "      Install from https://www.python.org/downloads/"
    exit 1
fi
python3 --version
echo "  ✓ Python OK"

# ---------------------------------------------------------------------------
# 2. Check Node.js
# ---------------------------------------------------------------------------
echo ""
echo "▶ Checking Node.js 18+…"
if ! command -v node &>/dev/null; then
    echo "  ❌  Node.js is not installed."
    echo "      Install from https://nodejs.org/ (LTS recommended)"
    exit 1
fi
NODE_VER=$(node --version | sed 's/v//')
NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "  ❌  Node.js 18+ required, found v${NODE_VER}"
    exit 1
fi
echo "  node $(node --version)  npm $(npm --version)"
echo "  ✓ Node.js OK"

# ---------------------------------------------------------------------------
# 3. Install Python dependencies
# ---------------------------------------------------------------------------
echo ""
echo "▶ Installing Python dependencies…"
pip3 install -r requirements.txt --quiet
echo "  ✓ Python dependencies installed"

# ---------------------------------------------------------------------------
# 4. Install Node.js (Mineflayer) dependencies
# ---------------------------------------------------------------------------
echo ""
echo "▶ Installing Node.js dependencies (Mineflayer)…"
npm install --silent
echo "  ✓ Node.js dependencies installed"

# ---------------------------------------------------------------------------
# 5. Create .env if it doesn't exist
# ---------------------------------------------------------------------------
echo ""
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "▶ Created .env from .env.example"
    echo "  ⚠  Edit .env (or ip.txt) with your server details before running!"
else
    echo "▶ .env already exists – skipping"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   Setup complete!                                ║"
echo "║                                                  ║"
echo "║   1. Put your server IP in ip.txt                ║"
echo "║   2. Edit .env and set PASSWORD, VERSION, etc.   ║"
echo "║   3. Run:  python3 main.py                       ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

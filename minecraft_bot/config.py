"""
config.py
=========
Centralised configuration for the Minecraft automation bot (pure Python).

Reads values from environment variables (with sensible defaults) and from
a plain-text ``ip.txt`` file (first non-empty line used as the server host).
All values are validated on import; bad config raises ConfigError immediately.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# ip.txt reader
# ---------------------------------------------------------------------------

def _read_ip_file(path: str = "ip.txt") -> Optional[str]:
    ip_path = Path(path)
    if not ip_path.exists():
        return None
    for line in ip_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


# ---------------------------------------------------------------------------
# Configuration values
# ---------------------------------------------------------------------------

_HOST_FROM_FILE: Optional[str] = _read_ip_file()

# Server connection
HOST:    str = os.environ.get("HOST") or _HOST_FROM_FILE or "localhost"
PORT:    int = int(os.environ.get("PORT", "25565"))
VERSION: str = os.environ.get("VERSION", "1.21.4")

# Authentication / registration
PASSWORD:  str = os.environ.get("PASSWORD", "Secure@Bot2025!")
TP_PLAYER: str = os.environ.get("TP_PLAYER", "AKMVyron")

# Bot behaviour
RIGHT_CLICK_PER_SECOND: float = float(os.environ.get("RIGHT_CLICK_PER_SECOND", "8"))
WAIT_AFTER_TP:          float = float(os.environ.get("WAIT_AFTER_TP",          "5"))
WAIT_FOR_KEY_DROP:      float = float(os.environ.get("WAIT_FOR_KEY_DROP",      "10"))
ROTATION_SPEED:         float = float(os.environ.get("ROTATION_SPEED",         "25"))  # °/s
RECONNECT_DELAY:        float = float(os.environ.get("RECONNECT_DELAY",        "5"))

# Safety timer — bot auto-disconnects N seconds after /tpaccept
AUTO_LEAVE_SECONDS: float = float(os.environ.get("AUTO_LEAVE_SECONDS", "100"))

# Concurrent bot instances
MAX_BOTS: int = int(os.environ.get("MAX_BOTS", "1"))

# Timeouts (seconds)
REGISTER_TIMEOUT:      float = float(os.environ.get("REGISTER_TIMEOUT",      "30"))
LOGIN_TIMEOUT:         float = float(os.environ.get("LOGIN_TIMEOUT",         "30"))
TP_WAIT_TIMEOUT:       float = float(os.environ.get("TP_WAIT_TIMEOUT",       "120"))
VAULT_OPEN_TIMEOUT:    float = float(os.environ.get("VAULT_OPEN_TIMEOUT",    "30"))
INVENTORY_SETTLE_DELAY: float = float(os.environ.get("INVENTORY_SETTLE_DELAY", "2"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate() -> None:
    """Validate all config values; raise ConfigError on the first problem."""
    if not HOST:
        raise ConfigError(
            "No server host configured. "
            "Set the HOST env var or put the server address in ip.txt."
        )
    if not (1 <= PORT <= 65535):
        raise ConfigError(f"PORT must be 1–65535, got {PORT!r}")
    if not VERSION:
        raise ConfigError("VERSION must not be empty (e.g. '1.21.4')")
    if not PASSWORD:
        raise ConfigError("PASSWORD must not be empty")
    if not TP_PLAYER:
        raise ConfigError("TP_PLAYER must not be empty")
    if RIGHT_CLICK_PER_SECOND <= 0:
        raise ConfigError("RIGHT_CLICK_PER_SECOND must be > 0")
    if WAIT_AFTER_TP < 0:
        raise ConfigError("WAIT_AFTER_TP must be >= 0")
    if WAIT_FOR_KEY_DROP < 0:
        raise ConfigError("WAIT_FOR_KEY_DROP must be >= 0")
    if ROTATION_SPEED <= 0:
        raise ConfigError("ROTATION_SPEED must be > 0")
    if MAX_BOTS < 1:
        raise ConfigError("MAX_BOTS must be >= 1")


validate()

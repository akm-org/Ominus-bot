"""
utils.py
========
Shared utility helpers for the Minecraft bot system.

Includes:
- Random username generation (6 chars, alphanumeric, no reuse within runtime)
- Async sleep shortcuts
- Misc helpers
"""

from __future__ import annotations

import asyncio
import math
import random
import string
import time
from typing import Set

# ---------------------------------------------------------------------------
# Username pool (runtime-wide deduplication)
# ---------------------------------------------------------------------------

_used_usernames: Set[str] = set()
_USERNAME_CHARS = string.ascii_letters + string.digits   # A-Z a-z 0-9
_USERNAME_LENGTH = 6


def generate_username() -> str:
    """
    Generate a random 6-character alphanumeric username.

    Characters are drawn from A-Z, a-z, 0-9 (no symbols).
    The same username is never returned twice within the same Python process.

    Returns
    -------
    str
        A fresh unique username like ``Ab82Kd``.

    Raises
    ------
    RuntimeError
        If the entire username space has been exhausted (extremely unlikely).
    """
    max_attempts = 100_000
    for _ in range(max_attempts):
        name = "".join(random.choices(_USERNAME_CHARS, k=_USERNAME_LENGTH))
        if name not in _used_usernames:
            _used_usernames.add(name)
            return name
    raise RuntimeError(
        f"Username pool exhausted after {max_attempts} attempts. "
        "This should be statistically impossible – check for a bug."
    )


def release_username(name: str) -> None:
    """
    Remove *name* from the used-username pool so it can be reused.

    Not called in normal flow (usernames are intentionally never reused),
    but exposed for testing and future extensibility.
    """
    _used_usernames.discard(name)


def used_username_count() -> int:
    """Return the number of usernames generated so far this session."""
    return len(_used_usernames)


# ---------------------------------------------------------------------------
# Async sleep helpers
# ---------------------------------------------------------------------------

async def sleep(seconds: float) -> None:
    """Async sleep for *seconds* (float precision)."""
    await asyncio.sleep(seconds)


async def sleep_ms(milliseconds: float) -> None:
    """Async sleep for *milliseconds*."""
    await asyncio.sleep(milliseconds / 1000.0)


# ---------------------------------------------------------------------------
# Angle helpers
# ---------------------------------------------------------------------------

def normalize_yaw(yaw: float) -> float:
    """
    Normalise *yaw* to the range ``[-180, 180)``.

    Minecraft uses degrees; this keeps the value in the conventional range
    expected by most Mineflayer / protocol functions.
    """
    yaw = yaw % 360
    if yaw >= 180:
        yaw -= 360
    return yaw


def degrees_to_radians(deg: float) -> float:
    """Convert degrees to radians."""
    return deg * math.pi / 180.0


def radians_to_degrees(rad: float) -> float:
    """Convert radians to degrees."""
    return rad * 180.0 / math.pi


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

class RateThrottle:
    """
    Simple token-bucket–style rate throttle.

    Useful for capping right-click frequency without busy-waiting.

    Parameters
    ----------
    rate:
        Maximum events per second.
    """

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate
        self._last: float = 0.0

    async def wait(self) -> None:
        """
        Async-sleep until the next allowed event slot.

        Does not block the event loop while waiting.
        """
        now = time.monotonic()
        elapsed = now - self._last
        remaining = self._interval - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last = time.monotonic()

    def reset(self) -> None:
        """Reset the internal timer so the next call fires immediately."""
        self._last = 0.0


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

def strip_minecraft_formatting(text: str) -> str:
    """
    Remove Minecraft ``§`` colour/format codes from *text*.

    Mineflayer sometimes returns chat messages that still contain legacy
    section-sign codes.  This strips them so comparisons work correctly.
    """
    result = []
    i = 0
    while i < len(text):
        if text[i] == "§" and i + 1 < len(text):
            i += 2          # skip § + format char
        else:
            result.append(text[i])
            i += 1
    return "".join(result)


def sanitise_chat(text: str) -> str:
    """
    Strip colour codes and normalise whitespace from a chat message.

    Parameters
    ----------
    text:
        Raw chat string from Mineflayer.

    Returns
    -------
    str
        Clean, lowercase, stripped string suitable for matching.
    """
    return strip_minecraft_formatting(text).strip().lower()

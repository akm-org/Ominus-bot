"""
rotation.py
===========
Smooth continuous yaw rotation for the bot — pure Python, no Node.js.

Sends ``PlayerLookPacket`` via the pyCraft protocol wrapper at ~60 fps so
the bot sweeps a full 360° view while right-clicking the vault.

Design notes
------------
- ``look_straight()`` zeros pitch before the loop starts (avoids staring
  at the sky or ground during the spin).
- The controller is reusable: ``start()`` → ``stop()`` → ``start()`` …
- Speed is in degrees per second (default: ``config.ROTATION_SPEED``).
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from logger import get_logger
import config
from utils import normalize_yaw

log = get_logger("Rotation")


class RotationController:
    """
    Drives continuous yaw rotation by sending PlayerLook packets.

    Parameters
    ----------
    proto:
        Live :class:`~protocol.MCProtocol` instance.
    speed:
        Rotation speed in degrees per second.
    """

    _FRAME_INTERVAL: float = 1.0 / 60.0   # 60 fps

    def __init__(self, proto, speed: Optional[float] = None) -> None:
        self._proto  = proto
        self._speed: float = speed if speed is not None else config.ROTATION_SPEED
        self._task:  Optional[asyncio.Task] = None
        self._running: bool = False
        self._current_yaw: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> asyncio.Task:
        """
        Launch the rotation loop as a background asyncio task.

        Calling ``start()`` while already running cancels the old loop
        and starts a fresh one.
        """
        self.stop()
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="rotation-loop")
        log.debug(f"Rotation started at {self._speed:.1f}°/s")
        return self._task

    def stop(self) -> None:
        """Stop the rotation loop.  The bot's direction is left as-is."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        log.debug("Rotation stopped")

    def look_straight(self) -> None:
        """Zero the pitch (look straight ahead) without changing yaw."""
        try:
            self._proto.send_look(self._current_yaw, 0.0)
            log.debug("Pitch zeroed – looking straight")
        except Exception as exc:  # noqa: BLE001
            log.warn(f"look_straight() failed: {exc}")

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Advance yaw by speed × dt each frame, send PlayerLookPacket."""
        last_time = time.monotonic()

        while self._running:
            now = time.monotonic()
            dt  = now - last_time
            last_time = now

            self._current_yaw = normalize_yaw(self._current_yaw + self._speed * dt)

            try:
                # Pitch = 0 → perfectly horizontal look
                self._proto.send_look(self._current_yaw, 0.0)
            except Exception as exc:  # noqa: BLE001
                log.warn(f"send_look() failed: {exc}")

            await asyncio.sleep(self._FRAME_INTERVAL)

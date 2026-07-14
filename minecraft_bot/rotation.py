"""
rotation.py
===========
Smooth 360-degree continuous rotation for the bot.

The bot looks perfectly straight (pitch = 0) and rotates around the Y axis
at a configurable speed (degrees/second).  Rotation is driven by a tight
async loop so it does not block other coroutines.

Design notes
------------
- Uses ``bot.look(yaw, pitch, force=True)`` on the Mineflayer JS object.
- Rotation continues until :meth:`RotationController.stop` is called.
- The controller is reusable: call ``start`` / ``stop`` on each cycle.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Optional

from logger import get_logger
import config
from utils import normalize_yaw

log = get_logger("Rotation")


class RotationController:
    """
    Drives smooth, continuous yaw rotation on a Mineflayer bot.

    Parameters
    ----------
    bot:
        The live Mineflayer bot proxy (from the ``javascript`` package).
    speed:
        Rotation speed in degrees per second.  Defaults to
        ``config.ROTATION_SPEED``.
    """

    # Target frame duration (seconds).  60 fps feels smooth while being
    # kind to the CPU.
    _FRAME_INTERVAL: float = 1.0 / 60.0

    def __init__(self, bot, speed: Optional[float] = None) -> None:
        self._bot = bot
        self._speed: float = speed if speed is not None else config.ROTATION_SPEED
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._current_yaw: float = 0.0   # degrees, Minecraft convention

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> asyncio.Task:
        """
        Launch the rotation loop as a background asyncio task.

        Safe to call multiple times; a second call cancels the previous task
        and starts a fresh one.

        Returns
        -------
        asyncio.Task
            The background task handle.
        """
        self.stop()          # cancel any previous loop
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="rotation-loop")
        log.debug(f"Rotation started at {self._speed:.1f}°/s")
        return self._task

    def stop(self) -> None:
        """
        Stop the rotation loop.

        The bot's look direction is left wherever it was when stop was called.
        """
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        log.debug("Rotation stopped")

    @property
    def is_running(self) -> bool:
        """True if the rotation loop is currently active."""
        return self._running and (self._task is not None) and (not self._task.done())

    def set_speed(self, degrees_per_second: float) -> None:
        """
        Change rotation speed on the fly (takes effect on the next frame).

        Parameters
        ----------
        degrees_per_second:
            New speed in degrees/second.  Must be > 0.
        """
        if degrees_per_second <= 0:
            raise ValueError("Speed must be > 0 degrees/second")
        self._speed = degrees_per_second

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """
        Core rotation loop.

        Advances the yaw by ``speed * elapsed`` degrees each frame, clamped
        to [-180, 180), and calls ``bot.look`` with pitch = 0.
        """
        last_time = time.monotonic()

        while self._running:
            now = time.monotonic()
            dt = now - last_time
            last_time = now

            # Advance yaw
            self._current_yaw = normalize_yaw(
                self._current_yaw + self._speed * dt
            )

            # Convert to radians for Mineflayer (it accepts degrees too but
            # the force parameter works more reliably with explicit values)
            yaw_rad = math.radians(self._current_yaw)

            try:
                # Pitch = 0 means looking perfectly straight ahead
                self._bot.look(yaw_rad, 0, True)
            except Exception as exc:  # noqa: BLE001
                log.warn(f"look() failed: {exc}")

            # Sleep until next frame
            await asyncio.sleep(self._FRAME_INTERVAL)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def look_straight(self) -> None:
        """
        Immediately point the bot perfectly straight (pitch = 0, yaw unchanged).

        Called once before the rotation loop starts so the bot doesn't look
        up/down while waiting.
        """
        try:
            yaw_rad = math.radians(self._current_yaw)
            self._bot.look(yaw_rad, 0, True)
            log.debug("Pitch zeroed – looking straight")
        except Exception as exc:  # noqa: BLE001
            log.warn(f"look_straight() failed: {exc}")

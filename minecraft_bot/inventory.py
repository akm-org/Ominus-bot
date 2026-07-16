"""
inventory.py
============
Container window detection — pure Python, no Node.js.

Listens for ``OpenWindowPacket`` delivered by :class:`~protocol.MCProtocol`
and exposes an async API for the vault state machine.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from logger import get_logger
import config

log = get_logger("Inventory")


class InventoryManager:
    """
    Monitors the bot's open container windows.

    The pyCraft protocol layer fires ``proto.window_opened`` (an asyncio
    Event) whenever the server sends an ``OpenWindowPacket``.  This class
    wraps that event with a clean reset/wait API.

    Parameters
    ----------
    proto:
        Live :class:`~protocol.MCProtocol` instance.
    """

    def __init__(self, proto) -> None:
        self._proto = proto

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """
        Clear window state before a fresh vault attempt.

        Prevents a stale event from a previous cycle triggering a
        false-positive window detection.
        """
        self._proto.window_opened.clear()
        self._proto.window_id = None
        log.debug("InventoryManager reset")

    async def wait_for_window(self, timeout: float = 30.0) -> bool:
        """
        Wait for the server to open a container window.

        Parameters
        ----------
        timeout:
            Maximum seconds to wait.

        Returns
        -------
        bool
            ``True`` if a window opened, ``False`` on timeout.
        """
        log.debug(f"Waiting for container window (timeout={timeout}s)…")
        try:
            await asyncio.wait_for(
                self._proto.window_opened.wait(), timeout=timeout
            )
            log.success("Container window opened — vault is open!")
            return True
        except asyncio.TimeoutError:
            log.warn(f"No window opened within {timeout}s")
            return False

    async def wait_for_rewards_and_close(
        self, settle_delay: Optional[float] = None
    ) -> None:
        """
        Wait for rewards to settle in the vault GUI, then close the window.

        Parameters
        ----------
        settle_delay:
            Seconds to wait after the window opens.  Defaults to
            ``config.INVENTORY_SETTLE_DELAY``.
        """
        delay = settle_delay if settle_delay is not None else config.INVENTORY_SETTLE_DELAY
        log.info(f"Waiting {delay}s for rewards to settle…")
        await asyncio.sleep(delay)
        await self.close_window()

    async def close_window(self) -> None:
        """Send a CloseWindowPacket to gracefully close the container."""
        try:
            from minecraft.networking.packets import serverbound
            wid = self._proto.window_id or 0
            cls = getattr(serverbound.play, "CloseWindowPacket", None)
            if cls and self._proto._conn:
                pkt = cls()
                pkt.window_id = wid
                self._proto._conn.write_packet(pkt)
                log.info(f"Sent CloseWindow (id={wid})")
            else:
                log.debug("CloseWindowPacket not available – skipping close")
        except Exception as exc:  # noqa: BLE001
            log.warn(f"close_window() error: {exc}")

    def is_window_open(self) -> bool:
        """Return ``True`` if a container window event has fired."""
        return self._proto.window_opened.is_set()

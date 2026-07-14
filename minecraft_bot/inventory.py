"""
inventory.py
============
Inventory and container window helpers for the Minecraft bot.

Responsibilities:
- Detect when a container (vault GUI) window opens
- Wait until rewards are settled inside the window
- Close the window gracefully
- Handle inventory errors without crashing
"""

from __future__ import annotations

import asyncio
from typing import Optional

from logger import get_logger
import config

log = get_logger("Inventory")


class InventoryManager:
    """
    Monitors and manages the bot's inventory and open container windows.

    Parameters
    ----------
    bot:
        Live Mineflayer bot proxy.
    """

    def __init__(self, bot) -> None:
        self._bot = bot
        self._window_opened: asyncio.Event = asyncio.Event()
        self._window_id: Optional[int] = None
        self._setup_listeners()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_listeners(self) -> None:
        """
        Attach Mineflayer event listeners for inventory/window events.

        Mineflayer fires:
        - ``windowOpen``  – a container GUI has been opened
        - ``windowClose`` – the current window has been closed
        """
        try:
            @self._bot.on("windowOpen")
            def _on_window_open(window):
                """Handle a container window being opened by the server."""
                try:
                    self._window_id = getattr(window, "id", None)
                    log.info(
                        f"Window opened: type={getattr(window, 'type', '?')} "
                        f"title={getattr(window, 'title', '?')!r} "
                        f"id={self._window_id}"
                    )
                    self._window_opened.set()
                except Exception as exc:  # noqa: BLE001
                    log.warn(f"Error in windowOpen handler: {exc}")

            @self._bot.on("windowClose")
            def _on_window_close(window):
                """Handle a container window being closed."""
                log.debug("Window closed")
                self._window_opened.clear()
                self._window_id = None

        except Exception as exc:  # noqa: BLE001
            log.warn(f"Could not attach inventory listeners: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """
        Reset state for a new cycle.

        Call this before each vault attempt so stale events from the
        previous cycle do not trigger a false-positive window detection.
        """
        self._window_opened.clear()
        self._window_id = None
        log.debug("InventoryManager reset")

    async def wait_for_window(self, timeout: float = 30.0) -> bool:
        """
        Await the next ``windowOpen`` event from the server.

        Parameters
        ----------
        timeout:
            Maximum seconds to wait before giving up.

        Returns
        -------
        bool
            ``True`` if a window opened within *timeout*, ``False`` on timeout.
        """
        log.debug(f"Waiting for container window (timeout={timeout}s)…")
        try:
            await asyncio.wait_for(self._window_opened.wait(), timeout=timeout)
            log.success("Container window opened – vault is open!")
            return True
        except asyncio.TimeoutError:
            log.warn(f"No window opened within {timeout}s")
            return False

    async def wait_for_rewards_and_close(
        self,
        settle_delay: float = None,
    ) -> None:
        """
        Wait for vault rewards to settle, then close the window.

        Parameters
        ----------
        settle_delay:
            Seconds to wait after the window opens before closing.
            Defaults to ``config.INVENTORY_SETTLE_DELAY``.
        """
        delay = settle_delay if settle_delay is not None else config.INVENTORY_SETTLE_DELAY
        log.info(f"Waiting {delay}s for rewards to settle…")
        await asyncio.sleep(delay)
        await self.close_window()

    async def close_window(self) -> None:
        """
        Close the currently open container window cleanly.

        Calls ``bot.closeWindow`` if a window is open.  Errors are caught
        and logged so they don't interrupt the main flow.
        """
        try:
            window = getattr(self._bot, "currentWindow", None)
            if window is not None:
                self._bot.closeWindow(window)
                log.info("Inventory closed")
            else:
                log.debug("No open window to close")
        except Exception as exc:  # noqa: BLE001
            log.warn(f"Error closing window: {exc}")

    def is_window_open(self) -> bool:
        """Return ``True`` if a container window is currently open."""
        return self._window_opened.is_set()

    def current_window_id(self) -> Optional[int]:
        """Return the ID of the currently open window, or ``None``."""
        return self._window_id

    async def list_items(self) -> list:
        """
        Return a list of items in the currently open window.

        Returns an empty list if no window is open or the query fails.
        Each item is the raw Mineflayer item object.
        """
        try:
            window = getattr(self._bot, "currentWindow", None)
            if window is None:
                return []
            items = list(window.items())
            log.debug(f"Window has {len(items)} item slots")
            return items
        except Exception as exc:  # noqa: BLE001
            log.warn(f"Error listing items: {exc}")
            return []

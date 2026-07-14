"""
vault.py
========
Ominous Vault interaction logic.

Responsibilities:
- Find the nearest vault block in the world
- Spam right-click at approximately RIGHT_CLICK_PER_SECOND rate
- Continue rotating while clicking
- Detect vault opening via the windowOpen event (delegated to InventoryManager)
- Stop clicking once the vault opens
"""

from __future__ import annotations

import asyncio
from typing import Optional, TYPE_CHECKING

from logger import get_logger
from utils import RateThrottle
import config

if TYPE_CHECKING:
    from inventory import InventoryManager
    from rotation import RotationController

log = get_logger("Vault")


class VaultInteractor:
    """
    Handles locating and right-clicking a Minecraft Ominous Vault block.

    The interactor spams right-click at the configured rate while the
    :class:`~rotation.RotationController` keeps the bot spinning.  As soon
    as ``InventoryManager`` reports a window open, clicking stops.

    Parameters
    ----------
    bot:
        Live Mineflayer bot proxy.
    inventory:
        Shared :class:`~inventory.InventoryManager` instance.
    rotation:
        Shared :class:`~rotation.RotationController` instance.
    """

    # Range (in blocks) within which a vault is considered reachable
    _REACH_RANGE: float = 5.0

    # Minecraft block IDs / names for the Ominous Vault
    # (checked case-insensitively)
    _VAULT_NAMES = {"vault", "ominous_vault"}

    def __init__(
        self,
        bot,
        inventory: "InventoryManager",
        rotation: "RotationController",
    ) -> None:
        self._bot = bot
        self._inventory = inventory
        self._rotation = rotation
        self._throttle = RateThrottle(config.RIGHT_CLICK_PER_SECOND)
        self._clicking: bool = False
        self._click_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def open_vault(self, timeout: float = None) -> bool:
        """
        Attempt to open the nearest vault block.

        Starts the rotation, spams right-click at the configured rate, and
        waits for an inventory window to open.  Stops automatically once
        the vault opens or *timeout* is exceeded.

        Parameters
        ----------
        timeout:
            Maximum seconds to attempt opening.  Defaults to
            ``config.VAULT_OPEN_TIMEOUT``.

        Returns
        -------
        bool
            ``True`` if the vault opened (window detected), ``False`` on timeout.
        """
        timeout = timeout if timeout is not None else config.VAULT_OPEN_TIMEOUT
        vault_block = self._find_vault()

        if vault_block is None:
            log.warn("No vault block found nearby – starting click spam anyway (bot may be looking at it)")
        else:
            log.info(f"Vault block located at {self._block_pos(vault_block)}")

        log.info(f"Spamming right-click at {config.RIGHT_CLICK_PER_SECOND}/s (timeout={timeout}s)…")

        # Reset inventory state for a fresh detection
        self._inventory.reset()

        # Start clicking in background
        self._clicking = True
        self._click_task = asyncio.create_task(
            self._click_loop(vault_block), name="vault-click-loop"
        )

        # Wait for the vault window to open OR timeout
        opened = await self._inventory.wait_for_window(timeout=timeout)

        # Stop clicking regardless
        await self._stop_clicking()

        if opened:
            log.success("Vault opened successfully!")
        else:
            log.error("Vault did not open within timeout")

        return opened

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_vault(self):
        """
        Search the nearby world for a vault block.

        Uses ``bot.findBlock`` with a radius of ``_REACH_RANGE`` blocks.
        Returns the block object or ``None`` if none found.
        """
        try:
            # Try each known vault name variant
            for name in self._VAULT_NAMES:
                block_type = self._bot.registry.blocksByName.get(name)
                if block_type is None:
                    continue
                block = self._bot.findBlock({
                    "matching": block_type.id,
                    "maxDistance": self._REACH_RANGE,
                })
                if block is not None:
                    return block
        except Exception as exc:  # noqa: BLE001
            log.warn(f"findBlock error: {exc}")
        return None

    async def _click_loop(self, vault_block) -> None:
        """
        Continuously right-click on the vault block until stopped.

        If the vault block reference is available, activates it via
        ``bot.activateBlock``.  Falls back to ``bot.activateEntity`` or
        ``bot.interact`` when the block reference is unavailable.
        """
        self._throttle.reset()
        click_count = 0

        while self._clicking:
            await self._throttle.wait()

            if not self._clicking:
                break

            try:
                if vault_block is not None:
                    # activateBlock right-clicks the specific block
                    self._bot.activateBlock(vault_block)
                else:
                    # Fallback: right-click whatever the crosshair is on
                    self._bot.activateEntity(
                        getattr(self._bot, "entityAtCursor", None)
                    )
            except Exception as exc:  # noqa: BLE001
                # Don't spam error logs – vault may not be in range yet
                if click_count % 16 == 0:
                    log.debug(f"Click #{click_count} error (may be normal): {exc}")

            click_count += 1
            if click_count % 8 == 0:
                log.debug(f"Clicks sent: {click_count}")

    async def _stop_clicking(self) -> None:
        """Cancel the click loop task and wait for it to finish."""
        self._clicking = False
        if self._click_task and not self._click_task.done():
            self._click_task.cancel()
            try:
                await self._click_task
            except asyncio.CancelledError:
                pass
        self._click_task = None

    @staticmethod
    def _block_pos(block) -> str:
        """Return a human-readable position string for a block."""
        try:
            p = block.position
            return f"({int(p.x)}, {int(p.y)}, {int(p.z)})"
        except Exception:  # noqa: BLE001
            return "(unknown)"

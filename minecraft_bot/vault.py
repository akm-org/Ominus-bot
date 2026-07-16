"""
vault.py
========
Ominous Vault interaction — pure Python, no Node.js.

Spams ``PlayerBlockPlacement`` packets (right-click) at every block position
within reach of the player.  Because the bot is teleported right next to the
vault by AKMVyron, spraying all nearby positions guarantees a hit without
needing to parse chunk data to locate the vault block.

Stops the moment the server opens a container window (``OpenWindowPacket``).
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
    from protocol import MCProtocol

log = get_logger("Vault")

# Faces to try when right-clicking each block (top, north, south, west, east)
_FACES = (1, 2, 3, 4, 5)


class VaultInteractor:
    """
    Right-clicks blocks near the player until a vault window opens.

    Parameters
    ----------
    proto:
        Live :class:`~protocol.MCProtocol` instance.
    inventory:
        Shared :class:`~inventory.InventoryManager`.
    rotation:
        Shared :class:`~rotation.RotationController`.
    """

    # Reach radius (blocks) — keeps within the server's reach distance
    _RADIUS: int = 2

    def __init__(self, proto: "MCProtocol", inventory: "InventoryManager",
                 rotation: "RotationController") -> None:
        self._proto    = proto
        self._inventory = inventory
        self._rotation  = rotation
        self._throttle  = RateThrottle(config.RIGHT_CLICK_PER_SECOND)
        self._clicking: bool = False
        self._click_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def open_vault(self, timeout: Optional[float] = None) -> bool:
        """
        Spam right-click nearby blocks and wait for a container window.

        Parameters
        ----------
        timeout:
            Maximum seconds to attempt.  Defaults to
            ``config.VAULT_OPEN_TIMEOUT``.

        Returns
        -------
        bool
            ``True`` if a window opened (vault unlocked), ``False`` on timeout.
        """
        timeout = timeout if timeout is not None else config.VAULT_OPEN_TIMEOUT
        log.info(
            f"Spamming right-click at {config.RIGHT_CLICK_PER_SECOND}/s "
            f"(radius={self._RADIUS}, timeout={timeout}s)…"
        )

        # Fresh detection — clear any stale window event
        self._inventory.reset()

        # Start clicking in the background
        self._clicking   = True
        self._click_task = asyncio.create_task(
            self._click_loop(), name="vault-click-loop"
        )

        # Wait for vault window OR timeout
        opened = await self._inventory.wait_for_window(timeout=timeout)

        # Stop clicking regardless of outcome
        await self._stop_clicking()

        if opened:
            log.success("Vault opened successfully!")
        else:
            log.error("Vault did not open within timeout")

        return opened

    # ------------------------------------------------------------------
    # Internal click loop
    # ------------------------------------------------------------------

    async def _click_loop(self) -> None:
        """
        Cycle through all reachable positions and right-click each face.

        Also clicks the block directly in front (using current yaw) so the
        rotation naturally sweeps the vault face into the click pattern.
        """
        self._throttle.reset()
        click_count = 0

        while self._clicking:
            positions = self._proto.nearby_block_positions(self._RADIUS)
            front     = self._proto.position_in_front(distance=1.5)
            if front not in positions:
                positions.append(front)

            for pos in positions:
                if not self._clicking:
                    return

                await self._throttle.wait()

                if not self._clicking:
                    return

                x, y, z = pos
                # Right-click the top face of every candidate block
                self._proto.send_block_place(x, y, z, face=1)
                # Also swing arm to keep the server session active
                self._proto.send_swing()

                click_count += 1
                if click_count % 16 == 0:
                    log.debug(f"Right-clicks sent: {click_count}")

    # ------------------------------------------------------------------
    # Stop helper
    # ------------------------------------------------------------------

    async def _stop_clicking(self) -> None:
        """Cancel the click loop and wait for it to exit."""
        self._clicking = False
        if self._click_task and not self._click_task.done():
            self._click_task.cancel()
            try:
                await self._click_task
            except asyncio.CancelledError:
                pass
        self._click_task = None

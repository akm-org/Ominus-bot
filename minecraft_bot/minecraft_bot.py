"""
minecraft_bot.py
================
Core Minecraft bot implementation using the Mineflayer Node.js library
bridged through the ``javascript`` Python package.

Each ``MinecraftBot`` instance represents a single bot lifecycle:
  Generate name → Join → Register → Login → Wait TP → Accept → Wait 5s
  → Rotate → Spam click → Vault opens → Disconnect → Done

The caller (``BotManager``) loops this as many times as needed.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum, auto
from typing import Optional, Callable

from javascript import require, On, Once, AsyncTask, start   # type: ignore

from logger import get_logger
from utils import generate_username, sanitise_chat, sleep
from rotation import RotationController
from inventory import InventoryManager
from vault import VaultInteractor
import config

log = get_logger("Bot")

# ---------------------------------------------------------------------------
# Mineflayer (loaded once at module level; shared across bots)
# ---------------------------------------------------------------------------
mineflayer = require("mineflayer")
pathfinder = None   # loaded lazily – only if needed in future


# ---------------------------------------------------------------------------
# Bot state machine
# ---------------------------------------------------------------------------

class BotState(Enum):
    IDLE        = auto()
    CONNECTING  = auto()
    REGISTERING = auto()
    LOGGING_IN  = auto()
    WAITING_TP  = auto()
    TELEPORTING = auto()
    ROTATING    = auto()
    VAULT       = auto()
    DISCONNECTING = auto()
    DONE        = auto()
    ERROR       = auto()


# ---------------------------------------------------------------------------
# MinecraftBot
# ---------------------------------------------------------------------------

class MinecraftBot:
    """
    Single-lifecycle Minecraft bot.

    Parameters
    ----------
    username:
        The in-game name to use for this session.
    on_done:
        Optional callback invoked when the lifecycle finishes (cleanly or
        with an error).  Signature: ``on_done(username: str, error: bool)``.
    """

    def __init__(
        self,
        username: str,
        on_done: Optional[Callable[[str, bool], None]] = None,
    ) -> None:
        self.username = username
        self._on_done = on_done
        self._bot = None
        self._state: BotState = BotState.IDLE
        self._state_lock = asyncio.Lock()

        # Events used to synchronise async steps
        self._spawned       = asyncio.Event()
        self._registered    = asyncio.Event()
        self._logged_in     = asyncio.Event()
        self._tp_accepted   = asyncio.Event()
        self._disconnected  = asyncio.Event()

        # Sub-controllers (created after spawn)
        self._rotation: Optional[RotationController] = None
        self._inventory: Optional[InventoryManager] = None
        self._vault: Optional[VaultInteractor] = None

        # Track start time for diagnostics
        self._start_time: float = 0.0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> BotState:
        """Current state of the bot lifecycle."""
        return self._state

    # ------------------------------------------------------------------
    # Main lifecycle entry point
    # ------------------------------------------------------------------

    async def run(self) -> bool:
        """
        Execute the full bot lifecycle from connection to disconnect.

        Returns
        -------
        bool
            ``True`` on a successful vault cycle, ``False`` if an error
            prevented completion.
        """
        self._start_time = time.monotonic()
        success = False

        try:
            log.info(f"[{self.username}] Starting lifecycle")
            await self._connect()
            await self._wait_spawn()
            await self._register()
            await self._login()
            await self._wait_for_tp()
            await self._accept_tp_delay()
            await self._wait_for_key_pickup()
            await self._do_vault()
            success = True

        except asyncio.CancelledError:
            log.warn(f"[{self.username}] Lifecycle cancelled")
            raise

        except Exception as exc:  # noqa: BLE001
            log.error(f"[{self.username}] Lifecycle error: {exc}")
            await self._set_state(BotState.ERROR)

        finally:
            await self._disconnect_bot()
            elapsed = time.monotonic() - self._start_time
            log.info(f"[{self.username}] Lifecycle finished in {elapsed:.1f}s (success={success})")
            if self._on_done:
                try:
                    self._on_done(self.username, not success)
                except Exception:  # noqa: BLE001
                    pass

        return success

    # ------------------------------------------------------------------
    # Step: connect
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """Create the Mineflayer bot and attach core event listeners."""
        await self._set_state(BotState.CONNECTING)
        log.info(f"[{self.username}] Connecting to {config.HOST}:{config.PORT} (v{config.VERSION})…")

        options = {
            "host":     config.HOST,
            "port":     config.PORT,
            "username": self.username,
            "version":  config.VERSION,
            "auth":     "offline",
            "hideErrors": False,
        }

        self._bot = mineflayer.createBot(options)
        self._attach_core_listeners()
        log.success(f"[{self.username}] Connected")

    # ------------------------------------------------------------------
    # Step: wait for spawn
    # ------------------------------------------------------------------

    async def _wait_spawn(self) -> None:
        """Wait for the bot to fully spawn in the world."""
        log.debug(f"[{self.username}] Waiting for spawn…")
        try:
            await asyncio.wait_for(self._spawned.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            raise RuntimeError("Timed out waiting for spawn")

        # Initialise sub-controllers now the bot object is live
        self._rotation  = RotationController(self._bot)
        self._inventory = InventoryManager(self._bot)
        self._vault     = VaultInteractor(self._bot, self._inventory, self._rotation)

    # ------------------------------------------------------------------
    # Step: register
    # ------------------------------------------------------------------

    async def _register(self) -> None:
        """Send /register command immediately after spawning."""
        await self._set_state(BotState.REGISTERING)
        cmd = f"/register {config.PASSWORD} {config.PASSWORD}"
        log.info(f"[{self.username}] Sending /register…")
        self._bot.chat(cmd)
        # Wait 2 s then continue regardless (server may say "already registered")
        await sleep(2)
        log.success(f"[{self.username}] Registered (or already registered)")

    # ------------------------------------------------------------------
    # Step: login
    # ------------------------------------------------------------------

    async def _login(self) -> None:
        """Send /login and wait for acknowledgement or simply proceed."""
        await self._set_state(BotState.LOGGING_IN)
        log.info(f"[{self.username}] Sending /login…")
        self._bot.chat(f"/login {config.PASSWORD}")
        # Give the server a moment to process the login
        await sleep(2)
        log.success(f"[{self.username}] Logged in")

    # ------------------------------------------------------------------
    # Step: wait for TP request from TP_PLAYER
    # ------------------------------------------------------------------

    async def _wait_for_tp(self) -> None:
        """
        Listen for a teleport request or /tpa message from ``config.TP_PLAYER``
        and automatically respond with /tpaccept.
        """
        await self._set_state(BotState.WAITING_TP)
        log.info(f"[{self.username}] Waiting for TP from {config.TP_PLAYER}…")

        self._tp_accepted.clear()
        self._attach_chat_listener()

        try:
            await asyncio.wait_for(
                self._tp_accepted.wait(),
                timeout=config.TP_WAIT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"No TP request from {config.TP_PLAYER} within "
                f"{config.TP_WAIT_TIMEOUT}s"
            )
        log.success(f"[{self.username}] TP accepted")

    # ------------------------------------------------------------------
    # Step: post-TP delay
    # ------------------------------------------------------------------

    async def _accept_tp_delay(self) -> None:
        """
        Wait ``config.WAIT_AFTER_TP`` seconds after the teleport before moving.

        This gives the server time to move the bot to the vault room.
        """
        await self._set_state(BotState.TELEPORTING)
        delay = config.WAIT_AFTER_TP
        log.info(f"[{self.username}] Teleported – waiting {delay}s before interacting…")
        await sleep(delay)

    # ------------------------------------------------------------------
    # Step: wait for operator to drop the Ominous Vault key
    # ------------------------------------------------------------------

    async def _wait_for_key_pickup(self) -> None:
        """
        Stand still for ``config.WAIT_FOR_KEY_DROP`` seconds so the operator
        has time to drop the Ominous Vault key on the ground.

        The bot remains stationary — it does not move or look around.
        The Mineflayer engine picks up dropped items automatically when they
        land within collection range, so no extra action is needed here.

        After the timer expires, the flow continues directly to vault
        interaction (rotation + right-click spam).
        """
        delay = config.WAIT_FOR_KEY_DROP
        log.info(
            f"[{self.username}] Waiting {delay}s for key drop — "
            f"drop the Ominous Vault key now!"
        )

        # Count down in 1-second increments so the log stays informative
        for remaining in range(int(delay), 0, -1):
            log.debug(f"[{self.username}] Key pickup window: {remaining}s remaining…")
            await sleep(1)

        # Handle any sub-second remainder
        leftover = delay - int(delay)
        if leftover > 0:
            await sleep(leftover)

        log.success(f"[{self.username}] Key pickup window finished – proceeding to vault")

    # ------------------------------------------------------------------
    # Step: vault interaction (rotate + click)
    # ------------------------------------------------------------------

    async def _do_vault(self) -> None:
        """
        Start rotating, spam right-click the vault, wait for it to open,
        collect rewards, then clean up.
        """
        await self._set_state(BotState.VAULT)
        log.info(f"[{self.username}] Starting vault interaction")

        # Level the pitch (look straight)
        self._rotation.look_straight()
        # Begin continuous rotation
        self._rotation.start()

        try:
            # Spam-click until the vault opens
            opened = await self._vault.open_vault(timeout=config.VAULT_OPEN_TIMEOUT)

            if opened:
                log.success(f"[{self.username}] Vault opened – collecting rewards…")
                await self._inventory.wait_for_rewards_and_close()
            else:
                log.error(f"[{self.username}] Vault failed to open")
        finally:
            self._rotation.stop()

    # ------------------------------------------------------------------
    # Step: disconnect
    # ------------------------------------------------------------------

    async def _disconnect_bot(self) -> None:
        """Cleanly disconnect from the server and free resources."""
        if self._bot is None:
            return
        await self._set_state(BotState.DISCONNECTING)
        log.info(f"[{self.username}] Disconnecting…")
        try:
            self._bot.quit("Cycle complete")
        except Exception:  # noqa: BLE001
            pass
        # Allow the disconnect packet to propagate
        await sleep(1)
        self._bot = None
        await self._set_state(BotState.DONE)
        log.success(f"[{self.username}] Disconnected")

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    def _attach_core_listeners(self) -> None:
        """Attach spawn / error / kick event listeners to the bot."""

        bot = self._bot

        @bot.once("spawn")
        def _on_spawn():
            """Fired once the bot receives its spawn position."""
            log.success(f"[{self.username}] Spawned in world")
            self._spawned.set()

        @bot.on("kicked")
        def _on_kicked(reason, logged_in):
            """Handle server kick; sets disconnected event."""
            log.warn(f"[{self.username}] Kicked: {reason}")
            self._disconnected.set()

        @bot.on("error")
        def _on_error(err):
            """Handle a network/protocol error."""
            log.error(f"[{self.username}] Error: {err}")

        @bot.on("end")
        def _on_end(reason):
            """Fired when the connection is fully closed."""
            log.info(f"[{self.username}] Connection ended: {reason}")
            self._disconnected.set()

    def _attach_chat_listener(self) -> None:
        """
        Watch for TP request messages from ``config.TP_PLAYER`` and respond.

        Handles common server formats:
        - ``/tpa`` request notifications
        - Direct ``[TP_PLAYER] /tpa`` messages
        - Generic "wants to teleport" messages
        """
        bot = self._bot
        tp_player_lower = config.TP_PLAYER.lower()

        @bot.on("chat")
        def _on_chat(username: str, message: str, *_):
            """
            Check every chat message for a TP request from the trusted player.
            """
            try:
                # Is this message authored by or about our TP player?
                if username.lower() == tp_player_lower:
                    clean = sanitise_chat(message)
                    if any(kw in clean for kw in ("/tpa", "tpa", "teleport")):
                        self._send_tpaccept()
                        return

                # Some servers broadcast system messages like:
                # "AKMVyron has requested to teleport to you."
                clean_msg = sanitise_chat(message)
                if tp_player_lower in clean_msg and any(
                    kw in clean_msg for kw in ("teleport", "tpa", "/tpa")
                ):
                    self._send_tpaccept()

            except Exception as exc:  # noqa: BLE001
                log.warn(f"[{self.username}] chat handler error: {exc}")

        @bot.on("message")
        def _on_message(json_msg, position):
            """
            Handle structured JSON chat messages (used by many servers for
            teleport request notifications).
            """
            try:
                text = ""
                # Try to extract plain text from the message object
                if hasattr(json_msg, "toString"):
                    text = sanitise_chat(str(json_msg.toString()))
                elif hasattr(json_msg, "extra"):
                    text = sanitise_chat(str(json_msg))

                if not text:
                    return

                if tp_player_lower in text and any(
                    kw in text for kw in ("teleport", "tpa", "tp request", "wants to tp")
                ):
                    self._send_tpaccept()

            except Exception as exc:  # noqa: BLE001
                log.debug(f"[{self.username}] message handler error: {exc}")

    def _send_tpaccept(self) -> None:
        """
        Send /tpaccept once and signal the waiting coroutine.

        Guards against double-sending with the ``_tp_accepted`` event.
        """
        if self._tp_accepted.is_set():
            return   # already accepted
        try:
            log.info(f"[{self.username}] Sending /tpaccept…")
            self._bot.chat("/tpaccept")
            self._tp_accepted.set()
        except Exception as exc:  # noqa: BLE001
            log.warn(f"[{self.username}] /tpaccept failed: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _set_state(self, state: BotState) -> None:
        """Thread-safe state transition with logging."""
        async with self._state_lock:
            self._state = state
            log.debug(f"[{self.username}] State → {state.name}")

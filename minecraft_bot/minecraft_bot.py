"""
minecraft_bot.py
================
Core Minecraft bot — pure Python, no Node.js.

Uses the pyCraft library (via protocol.py) to speak the Minecraft protocol
directly over TCP.  No JavaScript bridge required.

Lifecycle (per cycle):
  Generate name → Join → Register → Login
  → Wait for /tpa from AKMVyron (strict match, login messages ignored)
  → /tpaccept → Wait WAIT_AFTER_TP s → Wait WAIT_FOR_KEY_DROP s (key drop)
  → Rotate + Spam right-click Ominous Vault
  → Vault opens  OR  100 s auto-leave  OR  AKMVyron whispers "leave"
  → Disconnect → Repeat
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum, auto
from typing import Optional, Callable

from logger import get_logger
from utils import generate_username, sanitise_chat, sleep
from protocol import MCProtocol
from rotation import RotationController
from inventory import InventoryManager
from vault import VaultInteractor
import config

log = get_logger("Bot")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class BotState(Enum):
    IDLE          = auto()
    CONNECTING    = auto()
    REGISTERING   = auto()
    LOGGING_IN    = auto()
    WAITING_TP    = auto()   # TPA listener only active here
    TELEPORTING   = auto()
    KEY_DROP      = auto()
    VAULT         = auto()
    DISCONNECTING = auto()
    DONE          = auto()
    ERROR         = auto()


# ---------------------------------------------------------------------------
# MinecraftBot
# ---------------------------------------------------------------------------

class MinecraftBot:
    """
    Single-lifecycle Minecraft bot (pure Python / pyCraft).

    Parameters
    ----------
    username:
        In-game name for this session.
    on_done:
        Optional callback: ``on_done(username, had_error)`` called when
        the lifecycle finishes.
    """

    def __init__(
        self,
        username: str,
        on_done: Optional[Callable[[str, bool], None]] = None,
    ) -> None:
        self.username = username
        self._on_done = on_done
        self._proto: Optional[MCProtocol] = None
        self._state: BotState = BotState.IDLE
        self._state_lock = asyncio.Lock()

        # ── Lifecycle events ───────────────────────────────────────────────
        self._tp_accepted = asyncio.Event()
        self._leave_event = asyncio.Event()
        self._leave_reason: str = ""

        # ── Sub-controllers (created after spawn) ──────────────────────────
        self._rotation: Optional[RotationController] = None
        self._inventory: Optional[InventoryManager]  = None
        self._vault: Optional[VaultInteractor]       = None

        # ── Timing ────────────────────────────────────────────────────────
        self._start_time: float = 0.0
        self._auto_leave_task: Optional[asyncio.Task] = None

        # ── Chat listener task ────────────────────────────────────────────
        self._chat_listener_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> BotState:
        return self._state

    # ------------------------------------------------------------------
    # Main lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> bool:
        """
        Execute the full bot lifecycle.

        Returns ``True`` on a successful vault cycle, ``False`` otherwise.
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
            self._cancel_auto_leave()
            self._cancel_chat_listener()
            await self._disconnect_bot()
            elapsed = time.monotonic() - self._start_time
            log.info(
                f"[{self.username}] Lifecycle finished in {elapsed:.1f}s "
                f"(success={success})"
            )
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
        await self._set_state(BotState.CONNECTING)
        log.info(
            f"[{self.username}] Connecting to "
            f"{config.HOST}:{config.PORT} (v{config.VERSION})…"
        )
        self._proto = MCProtocol(self.username)
        await self._proto.connect()
        log.success(f"[{self.username}] Connection initiated")

    # ------------------------------------------------------------------
    # Step: wait for spawn
    # ------------------------------------------------------------------

    async def _wait_spawn(self) -> None:
        log.debug(f"[{self.username}] Waiting for spawn…")
        try:
            await asyncio.wait_for(self._proto.spawned.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            raise RuntimeError("Timed out waiting for spawn")

        log.success(f"[{self.username}] Spawned in world")

        self._rotation  = RotationController(self._proto)
        self._inventory = InventoryManager(self._proto)
        self._vault     = VaultInteractor(self._proto, self._inventory, self._rotation)

        # Start the chat dispatcher (single task drains the queue for all listeners)
        self._chat_listener_task = asyncio.create_task(
            self._chat_dispatcher(), name="chat-dispatcher"
        )

    # ------------------------------------------------------------------
    # Step: register
    # ------------------------------------------------------------------

    async def _register(self) -> None:
        await self._set_state(BotState.REGISTERING)
        log.info(f"[{self.username}] Sending /register…")
        self._proto.send_chat(f"/register {config.PASSWORD} {config.PASSWORD}")
        await sleep(2)
        log.success(f"[{self.username}] /register sent")

    # ------------------------------------------------------------------
    # Step: login
    # ------------------------------------------------------------------

    async def _login(self) -> None:
        await self._set_state(BotState.LOGGING_IN)
        log.info(f"[{self.username}] Sending /login…")
        self._proto.send_chat(f"/login {config.PASSWORD}")
        await sleep(2)
        log.success(f"[{self.username}] /login sent")

    # ------------------------------------------------------------------
    # Step: wait for /tpa  (strict — login-safe)
    # ------------------------------------------------------------------

    async def _wait_for_tp(self) -> None:
        """
        Enter WAITING_TP and block until a verified /tpa request arrives
        from ``config.TP_PLAYER``.

        The TPA listener is embedded in the chat dispatcher; it only fires
        when this bot is in WAITING_TP state.
        """
        await self._set_state(BotState.WAITING_TP)
        log.info(
            f"[{self.username}] Waiting for /tpa from {config.TP_PLAYER} "
            f"(timeout={config.TP_WAIT_TIMEOUT}s)…"
        )
        self._tp_accepted.clear()
        self._leave_event.clear()

        try:
            await asyncio.wait_for(
                self._tp_accepted.wait(),
                timeout=config.TP_WAIT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"No /tpa from {config.TP_PLAYER} within {config.TP_WAIT_TIMEOUT}s"
            )

        log.success(f"[{self.username}] /tpaccept sent — teleporting")

        # Start the 100 s auto-leave safety timer immediately after TP
        self._auto_leave_task = asyncio.create_task(
            self._auto_leave_timer(config.AUTO_LEAVE_SECONDS),
            name="auto-leave-timer",
        )

    # ------------------------------------------------------------------
    # Step: post-TP delay
    # ------------------------------------------------------------------

    async def _accept_tp_delay(self) -> None:
        await self._set_state(BotState.TELEPORTING)
        delay = config.WAIT_AFTER_TP
        log.info(f"[{self.username}] Teleported — waiting {delay}s…")
        await self._interruptible_sleep(delay)

    # ------------------------------------------------------------------
    # Step: key-drop window
    # ------------------------------------------------------------------

    async def _wait_for_key_pickup(self) -> None:
        """
        Stand still while the operator drops the Ominous Vault key.
        The bot's collision box will pick it up automatically once it lands.
        """
        await self._set_state(BotState.KEY_DROP)
        delay = config.WAIT_FOR_KEY_DROP
        log.info(
            f"[{self.username}] Waiting {delay}s for key drop — "
            f"drop the Ominous Vault key now!"
        )
        for remaining in range(int(delay), 0, -1):
            log.debug(f"[{self.username}] Key pickup: {remaining}s remaining…")
            await self._interruptible_sleep(1)

        leftover = delay - int(delay)
        if leftover > 0:
            await self._interruptible_sleep(leftover)

        log.success(f"[{self.username}] Key pickup window done — proceeding to vault")

    # ------------------------------------------------------------------
    # Step: vault interaction
    # ------------------------------------------------------------------

    async def _do_vault(self) -> None:
        """
        Rotate + spam right-click the vault.

        Exits when vault window opens, leave signal fires, or the auto-leave
        timer fires.
        """
        await self._set_state(BotState.VAULT)
        log.info(f"[{self.username}] Starting vault interaction")

        self._rotation.look_straight()
        self._rotation.start()

        try:
            vault_task = asyncio.create_task(
                self._vault.open_vault(timeout=config.VAULT_OPEN_TIMEOUT),
                name="vault-open",
            )
            leave_task = asyncio.create_task(
                self._leave_event.wait(), name="leave-wait"
            )

            done, pending = await asyncio.wait(
                {vault_task, leave_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if leave_task in done:
                log.info(
                    f"[{self.username}] Leave triggered ({self._leave_reason}) "
                    f"— disconnecting"
                )
            elif vault_task in done:
                opened = vault_task.result()
                if opened:
                    log.success(f"[{self.username}] Vault opened — collecting rewards…")
                    await self._inventory.wait_for_rewards_and_close()
                else:
                    log.error(f"[{self.username}] Vault did not open within timeout")

        finally:
            self._rotation.stop()

    # ------------------------------------------------------------------
    # Step: disconnect
    # ------------------------------------------------------------------

    async def _disconnect_bot(self) -> None:
        if self._proto is None:
            return
        await self._set_state(BotState.DISCONNECTING)
        log.info(f"[{self.username}] Disconnecting…")
        try:
            self._proto.disconnect()
        except Exception:  # noqa: BLE001
            pass
        await sleep(1)
        self._proto = None
        await self._set_state(BotState.DONE)
        log.success(f"[{self.username}] Disconnected")

    # ------------------------------------------------------------------
    # Chat dispatcher  (single asyncio task draining the chat queue)
    # ------------------------------------------------------------------

    async def _chat_dispatcher(self) -> None:
        """
        Continuously drain ``proto.chat_queue`` and route each message to
        the TPA listener and the leave listener.

        Running as one task avoids the need for multiple pyCraft listeners
        and keeps all state checks in asyncio (no thread-safety concerns).
        """
        tp_lower    = config.TP_PLAYER.lower()

        TPA_KEYWORDS = (
            "has requested to teleport",
            "sent a teleport request",
            "wants to teleport to you",
            "requested teleportation",
            "has requested a tp",
        )

        WHISPER_MARKERS = (
            "->", "whispers", "to you", "msg", "message", "dm", "pm",
        )

        while True:
            try:
                sender, text = await self._proto.chat_queue.get()
            except asyncio.CancelledError:
                break
            except Exception:
                continue

            clean = sanitise_chat(text)

            # ── TPA detection (only in WAITING_TP) ───────────────────────
            if self._state == BotState.WAITING_TP and not self._tp_accepted.is_set():
                # Pattern 1: server notification "AKMVyron has requested to teleport"
                tpa_notify = (
                    tp_lower in clean
                    and any(kw in clean for kw in TPA_KEYWORDS)
                )
                # Pattern 2: direct whisper "/tpa" from TP_PLAYER
                tpa_whisper = (
                    sender is not None
                    and sender.lower() == tp_lower
                    and clean.strip() in ("/tpa", "/tpa ")
                )

                if tpa_notify or tpa_whisper:
                    self._send_tpaccept()

            # ── Leave detection (any state post-login) ────────────────────
            if self._state not in (
                BotState.IDLE, BotState.CONNECTING,
                BotState.REGISTERING, BotState.LOGGING_IN,
            ):
                is_from_tp = (
                    (sender is not None and sender.lower() == tp_lower)
                    or tp_lower in clean
                )
                is_leave = "leave" in clean
                is_whisper = any(m in clean for m in WHISPER_MARKERS)

                # Plain chat "leave" from TP_PLAYER
                if sender and sender.lower() == tp_lower and clean.strip() == "leave":
                    self._request_leave("chat 'leave' command")

                # Whisper "leave" from TP_PLAYER
                elif is_from_tp and is_leave and is_whisper:
                    self._request_leave("private message 'leave'")

    # ------------------------------------------------------------------
    # TPA helper
    # ------------------------------------------------------------------

    def _send_tpaccept(self) -> None:
        """Send /tpaccept exactly once."""
        if self._tp_accepted.is_set():
            return
        try:
            log.info(f"[{self.username}] Sending /tpaccept…")
            self._proto.send_chat("/tpaccept")
            self._tp_accepted.set()
        except Exception as exc:  # noqa: BLE001
            log.warn(f"[{self.username}] /tpaccept failed: {exc}")

    # ------------------------------------------------------------------
    # Leave helpers
    # ------------------------------------------------------------------

    def _request_leave(self, reason: str) -> None:
        """Signal the vault loop to stop and disconnect."""
        if self._leave_event.is_set():
            return
        self._leave_reason = reason
        self._leave_event.set()
        log.info(f"[{self.username}] Leave requested: {reason}")

    # ------------------------------------------------------------------
    # Auto-leave timer
    # ------------------------------------------------------------------

    async def _auto_leave_timer(self, seconds: float) -> None:
        """Fire ``_leave_event`` after *seconds* (safety net)."""
        log.info(f"[{self.username}] Auto-leave timer started ({seconds}s)")
        interval, elapsed = 10.0, 0.0
        while elapsed < seconds:
            chunk = min(interval, seconds - elapsed)
            await asyncio.sleep(chunk)
            elapsed += chunk
            if elapsed < seconds:
                log.debug(
                    f"[{self.username}] Auto-leave in {seconds - elapsed:.0f}s…"
                )
        if not self._leave_event.is_set():
            self._request_leave(f"auto-leave after {seconds}s")

    def _cancel_auto_leave(self) -> None:
        if self._auto_leave_task and not self._auto_leave_task.done():
            self._auto_leave_task.cancel()
        self._auto_leave_task = None

    # ------------------------------------------------------------------
    # Chat listener cleanup
    # ------------------------------------------------------------------

    def _cancel_chat_listener(self) -> None:
        if self._chat_listener_task and not self._chat_listener_task.done():
            self._chat_listener_task.cancel()
        self._chat_listener_task = None

    # ------------------------------------------------------------------
    # Interruptible sleep
    # ------------------------------------------------------------------

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep for *seconds* but wake immediately if leave is requested."""
        if self._leave_event.is_set():
            raise RuntimeError(f"Leave requested: {self._leave_reason}")
        try:
            await asyncio.wait_for(self._leave_event.wait(), timeout=seconds)
            raise RuntimeError(f"Leave requested: {self._leave_reason}")
        except asyncio.TimeoutError:
            pass   # normal — full sleep completed

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    async def _set_state(self, state: BotState) -> None:
        async with self._state_lock:
            self._state = state
            log.debug(f"[{self.username}] State → {state.name}")

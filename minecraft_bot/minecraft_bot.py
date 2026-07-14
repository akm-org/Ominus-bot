"""
minecraft_bot.py
================
Core Minecraft bot implementation using the Mineflayer Node.js library
bridged through the ``javascript`` Python package.

Lifecycle (per cycle):
  Generate name → Join → Register → Login
  → Wait for /tpa ONLY (strict match, login messages ignored)
  → /tpaccept → Wait 5s → Wait 10s (key drop)
  → Rotate + Spam click
  → Vault opens  OR  100s auto-leave  OR  AKMVyron whispers "leave"
  → Disconnect → Repeat with new username
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


# ---------------------------------------------------------------------------
# Bot state machine
# ---------------------------------------------------------------------------

class BotState(Enum):
    IDLE          = auto()
    CONNECTING    = auto()
    REGISTERING   = auto()
    LOGGING_IN    = auto()
    WAITING_TP    = auto()   # ← TPA listener is only active here
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
    Single-lifecycle Minecraft bot.

    Parameters
    ----------
    username:
        The in-game name to use for this session.
    on_done:
        Optional callback invoked when the lifecycle finishes.
        Signature: ``on_done(username: str, error: bool)``.
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

        # ── Lifecycle sync events ──────────────────────────────────────────
        self._spawned       = asyncio.Event()
        self._tp_accepted   = asyncio.Event()

        # ── Leave signal ──────────────────────────────────────────────────
        # Set by:  (a) AKMVyron whispering "leave"
        #          (b) the 100-second auto-leave timer
        self._leave_event   = asyncio.Event()
        self._leave_reason: str = ""

        # ── Sub-controllers (created after spawn) ─────────────────────────
        self._rotation: Optional[RotationController] = None
        self._inventory: Optional[InventoryManager] = None
        self._vault: Optional[VaultInteractor] = None

        # ── Timing ────────────────────────────────────────────────────────
        self._start_time: float = 0.0
        self._auto_leave_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> BotState:
        """Current lifecycle state."""
        return self._state

    # ------------------------------------------------------------------
    # Main lifecycle entry point
    # ------------------------------------------------------------------

    async def run(self) -> bool:
        """
        Execute the full bot lifecycle from connection to disconnect.

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
            await self._wait_for_tp()       # strict TPA-only; attaches listeners
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
        """Create the Mineflayer bot and attach core event listeners."""
        await self._set_state(BotState.CONNECTING)
        log.info(
            f"[{self.username}] Connecting to "
            f"{config.HOST}:{config.PORT} (v{config.VERSION})…"
        )
        self._bot = mineflayer.createBot({
            "host":       config.HOST,
            "port":       config.PORT,
            "username":   self.username,
            "version":    config.VERSION,
            "auth":       "offline",
            "hideErrors": False,
        })
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

        self._rotation  = RotationController(self._bot)
        self._inventory = InventoryManager(self._bot)
        self._vault     = VaultInteractor(self._bot, self._inventory, self._rotation)

    # ------------------------------------------------------------------
    # Step: register
    # ------------------------------------------------------------------

    async def _register(self) -> None:
        """
        Send /register immediately after spawning.

        Waits 2 s then continues regardless — the server may already know
        this username.  We do NOT accept any TPA during this window.
        """
        await self._set_state(BotState.REGISTERING)
        log.info(f"[{self.username}] Sending /register…")
        self._bot.chat(f"/register {config.PASSWORD} {config.PASSWORD}")
        await sleep(2)
        log.success(f"[{self.username}] Registered (or already registered)")

    # ------------------------------------------------------------------
    # Step: login
    # ------------------------------------------------------------------

    async def _login(self) -> None:
        """
        Send /login and wait for the server to process it.

        We do NOT accept any TPA during this window.
        """
        await self._set_state(BotState.LOGGING_IN)
        log.info(f"[{self.username}] Sending /login…")
        self._bot.chat(f"/login {config.PASSWORD}")
        await sleep(2)
        log.success(f"[{self.username}] Logged in")

    # ------------------------------------------------------------------
    # Step: wait for /tpa from TP_PLAYER  (strict — login-safe)
    # ------------------------------------------------------------------

    async def _wait_for_tp(self) -> None:
        """
        Enter WAITING_TP state and block until a proper /tpa request arrives
        from ``config.TP_PLAYER``.

        STRICT rules — the bot will NOT accept a TP if:
        - The bot is still in REGISTERING or LOGGING_IN state
        - The message does not come from / about ``config.TP_PLAYER``
        - The message is a generic chat line that happens to contain "tpa"

        ONLY these message patterns trigger /tpaccept:
        1. Server notification containing TP_PLAYER's name AND one of the
           canonical TPA keywords (case-insensitive):
               "has requested", "wants to teleport", "sent a teleport request"
        2. A direct whisper/PM from TP_PLAYER whose body is exactly "/tpa"
           or starts with "/tpa ".

        Also attaches the leave listener (whisper "leave" from TP_PLAYER).
        """
        await self._set_state(BotState.WAITING_TP)
        log.info(
            f"[{self.username}] Waiting for /tpa from {config.TP_PLAYER} "
            f"(timeout={config.TP_WAIT_TIMEOUT}s)…"
        )

        self._tp_accepted.clear()
        self._leave_event.clear()

        # Attach both listeners now (leave listener stays active for whole session)
        self._attach_tpa_listener()
        self._attach_leave_listener()

        try:
            await asyncio.wait_for(
                self._tp_accepted.wait(),
                timeout=config.TP_WAIT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"No /tpa from {config.TP_PLAYER} within "
                f"{config.TP_WAIT_TIMEOUT}s"
            )

        log.success(f"[{self.username}] TP accepted")

        # ── Start 100-second auto-leave timer ─────────────────────────────
        # The timer fires if the cycle takes too long (safety net).
        self._auto_leave_task = asyncio.create_task(
            self._auto_leave_timer(config.AUTO_LEAVE_SECONDS),
            name="auto-leave-timer",
        )

    # ------------------------------------------------------------------
    # Step: post-TP delay
    # ------------------------------------------------------------------

    async def _accept_tp_delay(self) -> None:
        """
        Wait ``config.WAIT_AFTER_TP`` seconds after teleport.

        Respects the leave signal — disconnects immediately if it fires.
        """
        await self._set_state(BotState.TELEPORTING)
        delay = config.WAIT_AFTER_TP
        log.info(f"[{self.username}] Teleported – waiting {delay}s…")
        await self._interruptible_sleep(delay)

    # ------------------------------------------------------------------
    # Step: wait for key drop
    # ------------------------------------------------------------------

    async def _wait_for_key_pickup(self) -> None:
        """
        Stand still for ``config.WAIT_FOR_KEY_DROP`` seconds so the operator
        can drop the Ominous Vault key.

        Mineflayer picks up nearby items automatically.
        Respects the leave signal — disconnects immediately if it fires.
        """
        await self._set_state(BotState.KEY_DROP)
        delay = config.WAIT_FOR_KEY_DROP
        log.info(
            f"[{self.username}] Waiting {delay}s for key drop — "
            f"drop the Ominous Vault key now!"
        )
        for remaining in range(int(delay), 0, -1):
            log.debug(f"[{self.username}] Key pickup window: {remaining}s remaining…")
            await self._interruptible_sleep(1)

        leftover = delay - int(delay)
        if leftover > 0:
            await self._interruptible_sleep(leftover)

        log.success(f"[{self.username}] Key pickup window finished – proceeding to vault")

    # ------------------------------------------------------------------
    # Step: vault interaction
    # ------------------------------------------------------------------

    async def _do_vault(self) -> None:
        """
        Rotate + spam right-click the vault.

        Exits when the FIRST of these occurs:
        - Vault window opens (success)
        - ``_leave_event`` is set (whisper "leave" OR 100-second auto-timer)
        """
        await self._set_state(BotState.VAULT)
        log.info(f"[{self.username}] Starting vault interaction")

        self._rotation.look_straight()
        self._rotation.start()

        try:
            # Race: vault opens  vs  leave signal
            vault_task = asyncio.create_task(
                self._vault.open_vault(timeout=config.VAULT_OPEN_TIMEOUT),
                name="vault-open",
            )
            leave_task = asyncio.create_task(
                self._leave_event.wait(),
                name="leave-wait",
            )

            done, pending = await asyncio.wait(
                {vault_task, leave_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel whichever didn't finish
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if leave_task in done:
                # Leave was requested (whisper or auto-timer)
                log.info(
                    f"[{self.username}] Leave triggered: {self._leave_reason} "
                    f"– disconnecting"
                )
            elif vault_task in done:
                opened = vault_task.result()
                if opened:
                    log.success(f"[{self.username}] Vault opened – collecting rewards…")
                    await self._inventory.wait_for_rewards_and_close()
                else:
                    log.error(f"[{self.username}] Vault failed to open within timeout")

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
        await sleep(1)
        self._bot = None
        await self._set_state(BotState.DONE)
        log.success(f"[{self.username}] Disconnected")

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    def _attach_core_listeners(self) -> None:
        """Attach spawn / error / kick / end listeners."""
        bot = self._bot

        @bot.once("spawn")
        def _on_spawn():
            log.success(f"[{self.username}] Spawned in world")
            self._spawned.set()

        @bot.on("kicked")
        def _on_kicked(reason, logged_in):
            log.warn(f"[{self.username}] Kicked: {reason}")

        @bot.on("error")
        def _on_error(err):
            log.error(f"[{self.username}] Error: {err}")

        @bot.on("end")
        def _on_end(reason):
            log.info(f"[{self.username}] Connection ended: {reason}")

    def _attach_tpa_listener(self) -> None:
        """
        Attach a STRICT /tpa listener.

        Only fires when:
        - The bot is in WAITING_TP state
        - The message is a server notification that ``config.TP_PLAYER``
          sent a teleport request  (canonical keywords below)
        - OR a direct whisper from ``config.TP_PLAYER`` whose body is "/tpa"

        Does NOT fire on login/register messages or generic "tpa" mentions.
        """
        bot = self._bot
        tp_player_lower = config.TP_PLAYER.lower()

        # Server-side TPA notification keywords (all major TP plugins)
        TPA_NOTIFY_KEYWORDS = (
            "has requested to teleport",
            "sent a teleport request",
            "wants to teleport to you",
            "requested teleportation",
            "has requested a tp",
        )

        def _is_tpa_notification(text: str) -> bool:
            """Return True if text is a server TPA request notification."""
            t = text.lower()
            return tp_player_lower in t and any(kw in t for kw in TPA_NOTIFY_KEYWORDS)

        def _is_direct_tpa_whisper(sender: str, body: str) -> bool:
            """Return True if sender is TP_PLAYER and body is a /tpa command."""
            return (
                sender.lower() == tp_player_lower
                and body.strip().lower() in ("/tpa", "/tpa ")
            )

        @bot.on("chat")
        def _on_chat(username: str, message: str, *_):
            """Handle plain-text chat messages."""
            # Only act when genuinely waiting for a TP
            if self._state != BotState.WAITING_TP:
                return
            try:
                clean = sanitise_chat(message)
                # Direct whisper-style /tpa from the trusted player
                if _is_direct_tpa_whisper(username, message):
                    self._send_tpaccept()
                    return
                # Server notification that includes TP_PLAYER's name
                if _is_tpa_notification(clean):
                    self._send_tpaccept()
            except Exception as exc:  # noqa: BLE001
                log.warn(f"[{self.username}] tpa chat handler error: {exc}")

        @bot.on("message")
        def _on_message(json_msg, position):
            """Handle structured JSON chat messages (used by many server plugins)."""
            if self._state != BotState.WAITING_TP:
                return
            try:
                text = ""
                if hasattr(json_msg, "toString"):
                    text = sanitise_chat(str(json_msg.toString()))
                if not text:
                    return
                if _is_tpa_notification(text):
                    self._send_tpaccept()
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[{self.username}] tpa message handler error: {exc}")

    def _attach_leave_listener(self) -> None:
        """
        Listen for a private message (whisper) of "leave" from TP_PLAYER
        at any point after login.

        Multiple whisper formats are covered:
        - ``[AKMVyron -> you]: leave``
        - ``AKMVyron whispers to you: leave``
        - ``[AKMVyron]: leave``  (some servers use this for /msg)
        - Plain chat "leave" typed by AKMVyron (fallback)

        When detected, sets ``_leave_event`` so the vault loop exits and
        the bot disconnects immediately.
        """
        bot = self._bot
        tp_player_lower = config.TP_PLAYER.lower()

        def _request_leave(reason: str) -> None:
            if self._leave_event.is_set():
                return
            self._leave_reason = reason
            self._leave_event.set()
            log.info(
                f"[{self.username}] Leave requested by {config.TP_PLAYER}: {reason}"
            )

        @bot.on("chat")
        def _on_chat_leave(username: str, message: str, *_):
            """Catch plain-chat 'leave' from TP_PLAYER as a fallback."""
            try:
                if username.lower() != tp_player_lower:
                    return
                if sanitise_chat(message) == "leave":
                    _request_leave("chat 'leave' command")
            except Exception as exc:  # noqa: BLE001
                log.debug(f"[{self.username}] leave-chat handler error: {exc}")

        @bot.on("message")
        def _on_message_leave(json_msg, position):
            """
            Catch whisper/private-message 'leave' from TP_PLAYER.

            Most servers deliver /msg output as a JSON message event rather
            than a plain chat event, so we check the raw text here.
            """
            try:
                text = ""
                if hasattr(json_msg, "toString"):
                    text = str(json_msg.toString())

                if not text:
                    return

                text_lower = text.lower()
                clean = sanitise_chat(text_lower)

                # Must mention TP_PLAYER (sender) and contain "leave"
                if tp_player_lower not in clean:
                    return
                if "leave" not in clean:
                    return

                # Whisper / PM indicators used by common servers
                whisper_indicators = (
                    "->",          # [AKMVyron -> you]
                    "whispers",    # AKMVyron whispers to you
                    "to you",      # sent a message to you
                    "msg",         # /msg output
                    "message",
                    "dm",
                    "pm",
                )
                is_whisper = any(ind in clean for ind in whisper_indicators)

                if is_whisper:
                    _request_leave(f"private message 'leave'")
                # If none of the whisper indicators matched but the message
                # is exclusively from TP_PLAYER and says "leave", still act.
                # This covers servers that format /msg as plain chat.

            except Exception as exc:  # noqa: BLE001
                log.debug(f"[{self.username}] leave-message handler error: {exc}")

    # ------------------------------------------------------------------
    # Auto-leave timer
    # ------------------------------------------------------------------

    async def _auto_leave_timer(self, seconds: float) -> None:
        """
        Countdown timer that fires ``_leave_event`` after *seconds*.

        Started immediately after TPA is accepted.  Counts down every
        10 seconds so the console shows progress.
        """
        log.info(
            f"[{self.username}] Auto-leave timer started: {seconds}s"
        )
        interval = 10.0
        elapsed = 0.0

        while elapsed < seconds:
            remaining = seconds - elapsed
            chunk = min(interval, remaining)
            await asyncio.sleep(chunk)
            elapsed += chunk
            if elapsed < seconds:
                log.debug(
                    f"[{self.username}] Auto-leave in "
                    f"{seconds - elapsed:.0f}s…"
                )

        if not self._leave_event.is_set():
            self._leave_reason = f"auto-leave after {seconds}s"
            self._leave_event.set()
            log.info(
                f"[{self.username}] Auto-leave timer fired after {seconds}s"
            )

    def _cancel_auto_leave(self) -> None:
        """Cancel the auto-leave timer task if still running."""
        if self._auto_leave_task and not self._auto_leave_task.done():
            self._auto_leave_task.cancel()
        self._auto_leave_task = None

    # ------------------------------------------------------------------
    # TPA helper
    # ------------------------------------------------------------------

    def _send_tpaccept(self) -> None:
        """
        Send /tpaccept exactly once and signal the waiting coroutine.
        Double-send is guarded by the ``_tp_accepted`` event.
        """
        if self._tp_accepted.is_set():
            return
        try:
            log.info(f"[{self.username}] Sending /tpaccept…")
            self._bot.chat("/tpaccept")
            self._tp_accepted.set()
        except Exception as exc:  # noqa: BLE001
            log.warn(f"[{self.username}] /tpaccept failed: {exc}")

    # ------------------------------------------------------------------
    # Interruptible sleep
    # ------------------------------------------------------------------

    async def _interruptible_sleep(self, seconds: float) -> None:
        """
        Sleep for *seconds* but wake immediately if ``_leave_event`` is set.

        Used during TELEPORTING and KEY_DROP steps so a "leave" whisper or
        auto-timer always takes effect promptly rather than waiting for a
        long sleep to finish.
        """
        if self._leave_event.is_set():
            raise RuntimeError(f"Leave requested: {self._leave_reason}")

        try:
            await asyncio.wait_for(
                self._leave_event.wait(),
                timeout=seconds,
            )
            # leave_event fired before the sleep finished
            raise RuntimeError(f"Leave requested: {self._leave_reason}")
        except asyncio.TimeoutError:
            pass   # normal – full sleep completed

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    async def _set_state(self, state: BotState) -> None:
        """Thread-safe state transition with logging."""
        async with self._state_lock:
            self._state = state
            log.debug(f"[{self.username}] State → {state.name}")

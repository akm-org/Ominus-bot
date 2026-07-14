"""
bot_manager.py
==============
Orchestrates multiple concurrent bot lifecycle instances.

Each bot runs an infinite loop:
    generate name → run lifecycle → wait RECONNECT_DELAY → repeat

The manager supports:
- Spawning up to ``MAX_BOTS`` concurrent bot tasks
- Pause / resume all bots
- Graceful stop with in-flight task cancellation
- Cycle statistics (completed, failed, total)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from logger import get_logger
from utils import generate_username, sleep
from minecraft_bot import MinecraftBot
import config

log = get_logger("Manager")


# ---------------------------------------------------------------------------
# Dataclass for per-slot statistics
# ---------------------------------------------------------------------------

@dataclass
class SlotStats:
    """Tracking info for a single bot slot (slot_id → stats)."""
    slot_id:   int
    cycles:    int = 0
    successes: int = 0
    failures:  int = 0
    current_username: Optional[str] = None
    last_start: float = field(default_factory=time.monotonic)

    @property
    def success_rate(self) -> float:
        """Return success rate as a fraction 0.0–1.0."""
        return self.successes / self.cycles if self.cycles else 0.0


# ---------------------------------------------------------------------------
# BotManager
# ---------------------------------------------------------------------------

class BotManager:
    """
    Manages the full lifecycle of one or many bot slots.

    Parameters
    ----------
    num_bots:
        How many concurrent bot slots to run.  Defaults to ``config.MAX_BOTS``.
    """

    def __init__(self, num_bots: Optional[int] = None) -> None:
        self._num_bots = num_bots if num_bots is not None else config.MAX_BOTS
        self._slots: Dict[int, SlotStats] = {}
        self._tasks: Dict[int, asyncio.Task] = {}
        self._running = False
        self._paused  = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()   # not paused initially (event = "may run")
        self._stop_event  = asyncio.Event()

    # ------------------------------------------------------------------
    # Control API (called from main.py console loop)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start all configured bot slots.

        Each slot runs in its own asyncio Task with an infinite retry loop.
        Returns once all slots are launched (they keep running in background).
        """
        if self._running:
            log.warn("Manager already running")
            return
        self._running = True
        self._stop_event.clear()
        log.info(f"Starting {self._num_bots} bot slot(s)…")

        for slot_id in range(self._num_bots):
            stats = SlotStats(slot_id=slot_id)
            self._slots[slot_id] = stats
            task = asyncio.create_task(
                self._slot_loop(slot_id, stats),
                name=f"bot-slot-{slot_id}",
            )
            self._tasks[slot_id] = task

        log.success(f"{self._num_bots} slot(s) running")

    async def stop(self) -> None:
        """
        Signal all slots to stop and wait for them to finish.

        In-flight bot lifecycles are cancelled; the reconnect timer is
        interrupted so slots exit promptly.
        """
        log.info("Stopping all bots…")
        self._running = False
        self._stop_event.set()
        # Allow paused bots to wake up so they can see the stop signal
        self._pause_event.set()

        # Cancel and await all tasks
        for task in self._tasks.values():
            task.cancel()
        results = await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        log.success("All bots stopped")

    def pause(self) -> None:
        """
        Pause all bot slots after their current lifecycle step completes.

        Slots that are mid-lifecycle finish their current step, then block
        at the top of the loop until resumed.
        """
        if self._paused:
            log.warn("Already paused")
            return
        self._paused = True
        self._pause_event.clear()   # block future iterations
        log.info("⏸  All bots paused – current cycles will finish then wait")

    def resume(self) -> None:
        """Resume all paused bot slots."""
        if not self._paused:
            log.warn("Not paused")
            return
        self._paused = False
        self._pause_event.set()
        log.info("▶  All bots resumed")

    def status(self) -> str:
        """Return a human-readable status summary."""
        lines = [
            f"Manager status: {'PAUSED' if self._paused else 'RUNNING' if self._running else 'STOPPED'}",
            f"Slots: {self._num_bots}",
        ]
        for sid, stats in self._slots.items():
            lines.append(
                f"  Slot {sid}: user={stats.current_username or '(idle)'}  "
                f"cycles={stats.cycles}  ok={stats.successes}  "
                f"fail={stats.failures}  "
                f"rate={stats.success_rate:.0%}"
            )
        return "\n".join(lines)

    @property
    def is_running(self) -> bool:
        """True if the manager has been started and not stopped."""
        return self._running

    @property
    def is_paused(self) -> bool:
        """True if the manager is currently paused."""
        return self._paused

    # ------------------------------------------------------------------
    # Internal slot loop
    # ------------------------------------------------------------------

    async def _slot_loop(self, slot_id: int, stats: SlotStats) -> None:
        """
        Infinite retry loop for a single bot slot.

        On each iteration:
        1. Wait if paused
        2. Generate a fresh username
        3. Run the full bot lifecycle
        4. Record result
        5. Wait ``RECONNECT_DELAY`` seconds before the next cycle
        """
        log.debug(f"Slot {slot_id}: loop started")

        while not self._stop_event.is_set():
            # -------- pause gate --------
            try:
                await asyncio.wait_for(
                    self._wait_for_resume(), timeout=None
                )
            except asyncio.CancelledError:
                log.debug(f"Slot {slot_id}: cancelled during pause wait")
                return

            if self._stop_event.is_set():
                break

            # -------- generate username --------
            username = generate_username()
            stats.current_username = username
            stats.last_start = time.monotonic()
            log.info(f"Slot {slot_id}: Generated username: {username}")

            # -------- run bot --------
            stats.cycles += 1
            success = False
            try:
                bot = MinecraftBot(username=username)
                success = await bot.run()
            except asyncio.CancelledError:
                log.debug(f"Slot {slot_id}: bot task cancelled")
                return
            except Exception as exc:  # noqa: BLE001
                log.error(f"Slot {slot_id}: Unhandled exception in bot run: {exc}")

            # -------- record result --------
            if success:
                stats.successes += 1
                log.success(
                    f"Slot {slot_id}: Cycle complete ✓  "
                    f"(total={stats.cycles}, ok={stats.successes})"
                )
            else:
                stats.failures += 1
                log.warn(
                    f"Slot {slot_id}: Cycle failed ✗  "
                    f"(total={stats.cycles}, fail={stats.failures})"
                )

            stats.current_username = None

            # -------- reconnect delay --------
            if not self._stop_event.is_set():
                log.info(
                    f"Slot {slot_id}: Waiting {config.RECONNECT_DELAY}s before next cycle…"
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=config.RECONNECT_DELAY,
                    )
                    break   # stop event fired during delay
                except asyncio.TimeoutError:
                    pass    # normal – continue to next cycle

        log.debug(f"Slot {slot_id}: loop exited")

    async def _wait_for_resume(self) -> None:
        """Block until the pause event is set (i.e. not paused)."""
        await self._pause_event.wait()

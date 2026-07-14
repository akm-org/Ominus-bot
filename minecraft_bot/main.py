"""
main.py
=======
Entry point for the Minecraft Automation Bot.

Starts the BotManager and runs a simple interactive console loop that
accepts the following commands while bots are running:

    status  – print a summary of all bot slots
    pause   – pause all bots (finishes current step, then waits)
    resume  – resume paused bots
    stop    – gracefully shut down all bots and exit

Press Ctrl-C for an immediate (graceful) shutdown.
"""

from __future__ import annotations

import asyncio
import sys

from logger import get_logger
from bot_manager import BotManager
import config

log = get_logger("Main")


# ---------------------------------------------------------------------------
# Console command handler
# ---------------------------------------------------------------------------

async def console_loop(manager: BotManager) -> None:
    """
    Read commands from stdin in a background task.

    Runs until ``stop`` is received or the event loop is cancelled.
    Uses ``asyncio.StreamReader`` so stdin does not block the event loop.
    """
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)

    try:
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    except Exception as exc:  # noqa: BLE001
        log.warn(f"Console not available: {exc}")
        return

    log.info("Console ready – commands: status | pause | resume | stop")

    while True:
        try:
            line_bytes = await reader.readline()
            if not line_bytes:
                break                                  # EOF / pipe closed
            cmd = line_bytes.decode("utf-8", errors="replace").strip().lower()
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            log.warn(f"Console read error: {exc}")
            break

        if not cmd:
            continue

        if cmd == "status":
            print(manager.status())

        elif cmd == "pause":
            manager.pause()

        elif cmd == "resume":
            manager.resume()

        elif cmd == "stop":
            log.info("Stop command received")
            await manager.stop()
            break

        else:
            log.warn(f"Unknown command: {cmd!r}  (status | pause | resume | stop)")


# ---------------------------------------------------------------------------
# Bootstrap banner
# ---------------------------------------------------------------------------

def _print_banner() -> None:
    """Print a startup banner with config summary."""
    print(
        "\n"
        "╔══════════════════════════════════════════════════╗\n"
        "║       Minecraft Automation Bot  –  Python 3.13   ║\n"
        "╚══════════════════════════════════════════════════╝\n"
    )
    log.info(f"Server    : {config.HOST}:{config.PORT}  v{config.VERSION}")
    log.info(f"TP player : {config.TP_PLAYER}")
    log.info(f"Bots      : {config.MAX_BOTS}")
    log.info(f"Click rate: {config.RIGHT_CLICK_PER_SECOND}/s")
    log.info(f"Wait (TP) : {config.WAIT_AFTER_TP}s")
    print()


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------

async def main() -> None:
    """
    Application entry point.

    1. Print banner
    2. Start BotManager
    3. Run console loop until stop or Ctrl-C
    4. Graceful shutdown
    """
    _print_banner()

    manager = BotManager()

    # Start the manager (launches all bot slot tasks)
    await manager.start()

    # Run the interactive console (blocks until 'stop' or EOF)
    console_task = asyncio.create_task(console_loop(manager), name="console")

    try:
        # Wait until the console exits (stop command) or manager finishes
        await console_task
    except asyncio.CancelledError:
        log.info("Shutdown signal received")
    finally:
        # Ensure everything is cleaned up
        if manager.is_running:
            await manager.stop()
        console_task.cancel()
        try:
            await console_task
        except (asyncio.CancelledError, Exception):
            pass

    log.info("Goodbye!")


# ---------------------------------------------------------------------------
# Entry guard
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user – shutting down…")

"""
logger.py
=========
Coloured, timestamped logging for the Minecraft bot system.

All output goes to stdout with ANSI escape codes.  Each log level maps to a
distinct colour so operators can scan the console at a glance.

Usage::

    from logger import get_logger
    log = get_logger("MyBot")
    log.info("Connected")
    log.success("Vault opened")
    log.warn("Retrying…")
    log.error("Kicked!")
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# ANSI colour codes
# ---------------------------------------------------------------------------

class _Color:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    # Foreground
    BLACK   = "\033[30m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN    = "\033[36m"
    WHITE   = "\033[37m"
    # Bright foreground
    BRIGHT_RED     = "\033[91m"
    BRIGHT_GREEN   = "\033[92m"
    BRIGHT_YELLOW  = "\033[93m"
    BRIGHT_BLUE    = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN    = "\033[96m"
    BRIGHT_WHITE   = "\033[97m"


# ---------------------------------------------------------------------------
# Custom log levels
# ---------------------------------------------------------------------------

SUCCESS_LEVEL = 25   # between INFO (20) and WARNING (30)
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

class _ColorFormatter(logging.Formatter):
    """Applies per-level ANSI colouring to log records."""

    _LEVEL_COLORS = {
        logging.DEBUG:   _Color.BRIGHT_BLUE,
        logging.INFO:    _Color.BRIGHT_WHITE,
        SUCCESS_LEVEL:   _Color.BRIGHT_GREEN,
        logging.WARNING: _Color.BRIGHT_YELLOW,
        logging.ERROR:   _Color.BRIGHT_RED,
        logging.CRITICAL: _Color.BOLD + _Color.RED,
    }

    _NAME_COLOR = _Color.BRIGHT_CYAN
    _TIME_COLOR = _Color.CYAN

    def format(self, record: logging.LogRecord) -> str:
        ts   = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        lvl  = record.levelname[:7]
        col  = self._LEVEL_COLORS.get(record.levelno, _Color.RESET)
        name = record.name

        time_part = f"{self._TIME_COLOR}[{ts}]{_Color.RESET}"
        name_part = f"{self._NAME_COLOR}[{name}]{_Color.RESET}"
        msg_part  = f"{col}{record.getMessage()}{_Color.RESET}"

        return f"{time_part} {name_part} {msg_part}"


# ---------------------------------------------------------------------------
# BotLogger – thin wrapper around stdlib Logger
# ---------------------------------------------------------------------------

class BotLogger:
    """
    Wrapper that adds a ``success`` convenience method on top of the standard
    :class:`logging.Logger`.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    # ----- standard levels -----

    def debug(self, msg: str, *args, **kwargs) -> None:
        """Log a DEBUG message."""
        self._log.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        """Log an INFO message."""
        self._log.info(msg, *args, **kwargs)

    def warn(self, msg: str, *args, **kwargs) -> None:
        """Log a WARNING message."""
        self._log.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        """Log an ERROR message."""
        self._log.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        """Log a CRITICAL message."""
        self._log.critical(msg, *args, **kwargs)

    # ----- custom level -----

    def success(self, msg: str, *args, **kwargs) -> None:
        """Log a SUCCESS message (level 25, between INFO and WARNING)."""
        self._log.log(SUCCESS_LEVEL, msg, *args, **kwargs)

    # ----- passthrough -----

    @property
    def name(self) -> str:
        """Return the underlying logger name."""
        return self._log.name


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_configured = False


def _ensure_root_configured() -> None:
    """Configure the root logger once (thread-safe)."""
    global _configured
    with _lock:
        if _configured:
            return
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_ColorFormatter())
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        _configured = True


def get_logger(name: str, level: int = logging.DEBUG) -> BotLogger:
    """
    Return a :class:`BotLogger` for *name*.

    Parameters
    ----------
    name:
        Logger name shown in ``[name]`` brackets.
    level:
        Minimum log level; defaults to DEBUG.
    """
    _ensure_root_configured()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    return BotLogger(logger)


# ---------------------------------------------------------------------------
# Module-level convenience logger
# ---------------------------------------------------------------------------
log = get_logger("System")

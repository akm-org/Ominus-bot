"""
protocol.py
===========
Async-friendly wrapper around the pyCraft Minecraft protocol library.

pyCraft runs its networking in a background thread.  This module bridges
that thread back to asyncio using ``loop.call_soon_threadsafe()`` so all
game events are safely delivered to the async state machine.

Supports offline-mode servers (no Mojang authentication required).
"""

from __future__ import annotations

import asyncio
import math
from typing import Optional, List, Tuple

from minecraft.networking.connection import Connection
from minecraft.networking.packets import clientbound, serverbound

from logger import get_logger
import config

log = get_logger("Protocol")


class MCProtocol:
    """
    Async wrapper around a pyCraft ``Connection``.

    Parameters
    ----------
    username:
        In-game name for this session (offline mode – no auth).
    """

    def __init__(self, username: str) -> None:
        self.username = username
        self._conn: Optional[Connection] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # ── Asyncio events (set from pyCraft thread via call_soon_threadsafe) ──
        self.spawned:       asyncio.Event = asyncio.Event()
        self.window_opened: asyncio.Event = asyncio.Event()
        self.disconnected:  asyncio.Event = asyncio.Event()

        # ── Player state (updated from position packets) ───────────────────
        self.x:         float = 0.0
        self.y:         float = 0.0
        self.z:         float = 0.0
        self.yaw:       float = 0.0   # degrees (Minecraft convention)
        self.pitch:     float = 0.0   # degrees
        self.on_ground: bool  = True

        # ── Window state ──────────────────────────────────────────────────
        self.window_id: Optional[int] = None

        # ── Incoming chat queue ───────────────────────────────────────────
        # Each item: (sender: str | None, text: str)
        self.chat_queue: asyncio.Queue[Tuple[Optional[str], str]] = asyncio.Queue()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        Open a connection to the server (offline mode, no auth token).

        pyCraft starts its own networking thread; this coroutine returns
        once the connection handshake is initiated.
        """
        self._loop = asyncio.get_running_loop()

        self._conn = Connection(
            config.HOST,
            config.PORT,
            username=self.username,
            initial_version=config.VERSION,
        )

        self._register_listeners()

        # connect() is blocking (spawns the networking thread)
        await self._loop.run_in_executor(None, self._conn.connect)
        log.info(f"[{self.username}] Connection initiated to "
                 f"{config.HOST}:{config.PORT} (v{config.VERSION})")

    def disconnect(self) -> None:
        """Disconnect from the server.  Safe to call from any thread."""
        try:
            if self._conn:
                self._conn.disconnect()
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self.username}] disconnect() error (ignored): {exc}")
        finally:
            self._set_event(self.disconnected)

    # ------------------------------------------------------------------
    # Packet sending  (thread-safe – pyCraft write_packet is thread-safe)
    # ------------------------------------------------------------------

    def send_chat(self, message: str) -> None:
        """
        Send *message* to the server (chat or command).

        For commands starting with '/', this automatically tries the
        post-1.19 ``ChatCommandPacket`` first, falling back to the
        universal ``ChatPacket`` if the newer class is not available.
        """
        if not self._conn:
            return
        try:
            # Post-1.19: commands go through a separate packet type on
            # some server configurations – try both so offline servers work.
            if message.startswith("/"):
                cmd_cls = getattr(serverbound.play, "ChatCommandPacket", None)
                if cmd_cls is not None:
                    try:
                        pkt = cmd_cls()
                        # Strip the leading slash for the command packet
                        pkt.command = message[1:]
                        self._conn.write_packet(pkt)
                        return
                    except Exception:
                        pass  # fall through to ChatPacket

            pkt = serverbound.play.ChatPacket()
            pkt.message = message
            self._conn.write_packet(pkt)

        except Exception as exc:  # noqa: BLE001
            log.warn(f"[{self.username}] send_chat error: {exc}")

    def send_look(self, yaw_degrees: float, pitch_degrees: float = 0.0) -> None:
        """
        Send a ``PlayerLook`` packet to update the bot's head direction.

        Parameters
        ----------
        yaw_degrees:
            Horizontal angle in Minecraft degrees (0=south, 90=west …).
        pitch_degrees:
            Vertical angle (-90=up, 0=straight, 90=down).
        """
        if not self._conn:
            return
        try:
            self.yaw   = yaw_degrees
            self.pitch = pitch_degrees
            pkt = serverbound.play.PlayerLookPacket()
            pkt.yaw        = yaw_degrees
            pkt.pitch      = pitch_degrees
            pkt.on_ground  = self.on_ground
            self._conn.write_packet(pkt)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self.username}] send_look error: {exc}")

    def send_block_place(
        self,
        x: int,
        y: int,
        z: int,
        face: int = 1,
        hand: int = 0,
    ) -> None:
        """
        Send a ``PlayerBlockPlacement`` (right-click) packet.

        Parameters
        ----------
        x, y, z : int
            Target block coordinates.
        face : int
            Block face to click (0=bottom, 1=top, 2=north, 3=south,
            4=west, 5=east).  Defaults to top face (1).
        hand : int
            0 = main hand, 1 = off-hand.
        """
        if not self._conn:
            return
        try:
            pkt = serverbound.play.PlayerBlockPlacementPacket()
            pkt.position          = (x, y, z)
            pkt.face              = face
            pkt.hand              = hand
            pkt.cursor_position_x = 0.5
            pkt.cursor_position_y = 0.5
            pkt.cursor_position_z = 0.5
            self._conn.write_packet(pkt)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self.username}] send_block_place({x},{y},{z}) error: {exc}")

    def send_swing(self, hand: int = 0) -> None:
        """
        Send an arm-swing animation (keeps the server session alive during
        long idle periods and can help trigger block interactions).
        """
        if not self._conn:
            return
        try:
            cls = getattr(serverbound.play, "AnimationPacket", None)
            if cls:
                pkt = cls()
                pkt.hand = hand
                self._conn.write_packet(pkt)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self.username}] send_swing error: {exc}")

    # ------------------------------------------------------------------
    # Listener registration
    # ------------------------------------------------------------------

    def _register_listeners(self) -> None:
        cb = clientbound.play

        # Spawn / position correction
        self._try_listen(cb, "PlayerPositionAndLookPacket", self._on_position)

        # Chat – multiple variants across protocol versions
        for name in (
            "ChatMessagePacket",
            "SystemChatMessagePacket",
            "PlayerChatMessagePacket",
            "MapChunkPacket",  # deliberately skipped; listed to document we don't use it
        ):
            if name == "MapChunkPacket":
                continue   # we don't process chunks
            cls = getattr(cb, name, None)
            if cls is not None:
                self._try_listen(cb, name, self._on_chat)

        # Container window opened
        for name in ("OpenWindowPacket", "WindowOpenPacket"):
            if getattr(cb, name, None) is not None:
                self._try_listen(cb, name, self._on_window_open)
                break

        # Server-initiated disconnect
        self._try_listen(cb, "DisconnectPacket", self._on_disconnect)

    def _try_listen(self, module, class_name: str, handler) -> None:
        """Register a listener, silently ignoring unknown packet classes."""
        cls = getattr(module, class_name, None)
        if cls is None:
            return
        try:
            self._conn.register_packet_listener(handler, cls)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"Could not register listener for {class_name}: {exc}")

    # ------------------------------------------------------------------
    # Packet handlers  (called from pyCraft's networking thread)
    # ------------------------------------------------------------------

    def _on_position(self, packet) -> None:
        """Handle PlayerPositionAndLookPacket: update state, confirm teleport."""
        try:
            self.x     = float(getattr(packet, "x",     self.x))
            self.y     = float(getattr(packet, "y",     self.y))
            self.z     = float(getattr(packet, "z",     self.z))
            self.yaw   = float(getattr(packet, "yaw",   self.yaw))
            self.pitch = float(getattr(packet, "pitch", self.pitch))

            # Confirm the teleport (mandatory since Minecraft 1.9)
            tid = getattr(packet, "teleport_id", None)
            if tid is not None and self._conn:
                try:
                    confirm = serverbound.play.TeleportConfirmPacket()
                    confirm.teleport_id = tid
                    self._conn.write_packet(confirm)
                except Exception:  # noqa: BLE001
                    pass

        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self.username}] _on_position error: {exc}")
        finally:
            # Fire the spawned event (first position packet = spawned)
            self._set_event(self.spawned)

    def _on_chat(self, packet) -> None:
        """Handle any chat / system message packet."""
        try:
            text = self._extract_text(packet)
            if not text:
                return
            sender = self._extract_sender(packet)
            self._enqueue(self.chat_queue, (sender, text))
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self.username}] _on_chat error: {exc}")

    def _on_window_open(self, packet) -> None:
        """Handle OpenWindowPacket: a container GUI was opened."""
        try:
            self.window_id = getattr(packet, "window_id", None)
            log.info(f"[{self.username}] Container window opened (id={self.window_id})")
            self._set_event(self.window_opened)
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[{self.username}] _on_window_open error: {exc}")

    def _on_disconnect(self, packet) -> None:
        """Handle a server-initiated disconnect."""
        reason = getattr(packet, "json_data", "unknown")
        log.info(f"[{self.username}] Server disconnected us: {reason}")
        self._set_event(self.disconnected)

    # ------------------------------------------------------------------
    # Thread → asyncio bridge helpers
    # ------------------------------------------------------------------

    def _set_event(self, event: asyncio.Event) -> None:
        """Set an asyncio Event from pyCraft's networking thread."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(event.set)

    def _enqueue(self, queue: asyncio.Queue, item) -> None:
        """Put an item into an asyncio Queue from pyCraft's networking thread."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(queue.put_nowait, item)

    # ------------------------------------------------------------------
    # Packet text extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(packet) -> str:
        """
        Best-effort extraction of plain text from any chat packet.

        Tries common attribute names used across pyCraft versions.
        """
        for attr in ("json_data", "chat_message", "content", "message", "data"):
            val = getattr(packet, attr, None)
            if val is None:
                continue
            if isinstance(val, str):
                # Strip Minecraft §-colour codes
                return MCProtocol._strip_codes(val)
            # May be a ChatMessage object with a __str__
            try:
                return MCProtocol._strip_codes(str(val))
            except Exception:
                continue
        return ""

    @staticmethod
    def _extract_sender(packet) -> Optional[str]:
        """Try to extract the sender username from a chat packet."""
        for attr in ("sender", "username", "player_name"):
            val = getattr(packet, attr, None)
            if val:
                return str(val)
        return None

    @staticmethod
    def _strip_codes(text: str) -> str:
        """Remove Minecraft §-colour/format codes from *text*."""
        result = []
        i = 0
        while i < len(text):
            if text[i] == "§" and i + 1 < len(text):
                i += 2
            else:
                result.append(text[i])
                i += 1
        return "".join(result)

    # ------------------------------------------------------------------
    # Position helpers for vault interaction
    # ------------------------------------------------------------------

    def nearby_block_positions(self, radius: int = 2) -> List[Tuple[int, int, int]]:
        """
        Return integer block positions within *radius* blocks of the player.

        Called by VaultInteractor to build a list of candidates to
        right-click so the vault is hit without needing chunk data lookup.
        """
        bx, by, bz = int(math.floor(self.x)), int(math.floor(self.y)), int(math.floor(self.z))
        positions: List[Tuple[int, int, int]] = []
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if math.sqrt(dx * dx + dz * dz) <= radius:
                    for dy in (-1, 0, 1):
                        positions.append((bx + dx, by + dy, bz + dz))
        return positions

    def position_in_front(self, distance: float = 1.5) -> Tuple[int, int, int]:
        """
        Return the block coordinate directly in front of the player.

        Uses the current yaw to project forward by *distance* blocks.
        """
        yaw_rad = math.radians(self.yaw)
        fx = self.x - math.sin(yaw_rad) * distance
        fz = self.z + math.cos(yaw_rad) * distance
        return (int(math.floor(fx)), int(math.floor(self.y)), int(math.floor(fz)))

# Minecraft Automation Bot — Pure Python

Automates endless Ominous Vault farming on an offline-mode Minecraft server.
**No Node.js, no npm, no JavaScript bridge** — 100% Python via the
[pyCraft](https://github.com/ammaraskar/pyCraft) protocol library.

---

## Cycle flow

```
Generate random username
        │
        ▼
  Connect (offline mode)
        │
        ▼
  /register <password> <password>
        │
        ▼
  /login <password>
        │
        ▼
  Wait for /tpa from AKMVyron   ◄── strict match only; login msgs ignored
        │
        ▼
  /tpaccept  →  wait WAIT_AFTER_TP s
        │
        ▼
  Wait WAIT_FOR_KEY_DROP s      ◄── drop Ominous Vault key here
        │
        ▼
  Rotate 360° + spam right-click nearby blocks
        │
   ┌────┴─────────────┬─────────────────────────────────┐
   │                  │                                 │
Vault opens    100s auto-leave          AKMVyron whispers "leave"
   │                  │                                 │
   └────────┬─────────┘                                 │
            ▼                                           │
      Disconnect ◄──────────────────────────────────────┘
            │
            ▼
  Wait RECONNECT_DELAY s  →  repeat with NEW username
```

---

## Requirements

- Python 3.9+
- An offline-mode Minecraft server (1.21.4 recommended)
- **No Node.js required**

---

## Setup

```bash
cd minecraft_bot

# 1. Install Python dependencies (pyCraft + python-dotenv)
pip install -r requirements.txt

# OR run the one-shot setup script:
bash setup.sh

# 2. Put your server address in ip.txt
echo "your.server.ip" > ip.txt

# 3. (Optional) copy and edit .env
cp .env.example .env
# Edit .env with your password, TP player name, etc.

# 4. Run
python3 main.py
```

---

## Configuration

All settings can be placed in `.env` or set as environment variables.
`ip.txt` overrides `HOST` for convenience.

| Variable | Default | Description |
|---|---|---|
| `HOST` | `localhost` | Server IP / hostname (or use `ip.txt`) |
| `PORT` | `25565` | Server port |
| `VERSION` | `1.21.4` | Minecraft protocol version |
| `PASSWORD` | `Secure@Bot2025!` | `/register` and `/login` password |
| `TP_PLAYER` | `AKMVyron` | Only accept /tpa from this player |
| `RIGHT_CLICK_PER_SECOND` | `8` | Right-click rate for vault |
| `WAIT_AFTER_TP` | `5` | Seconds to wait after teleport |
| `WAIT_FOR_KEY_DROP` | `10` | Seconds for operator to drop vault key |
| `ROTATION_SPEED` | `25` | Degrees per second while spinning |
| `RECONNECT_DELAY` | `5` | Seconds between cycles |
| `AUTO_LEAVE_SECONDS` | `100` | Hard timeout after /tpaccept |
| `MAX_BOTS` | `1` | Concurrent bot slots |
| `TP_WAIT_TIMEOUT` | `120` | Seconds to wait for /tpa |
| `VAULT_OPEN_TIMEOUT` | `30` | Seconds of clicking before giving up |
| `INVENTORY_SETTLE_DELAY` | `2` | Seconds to wait in vault GUI |

---

## Console commands

While the bot is running, type into the terminal:

| Command | Effect |
|---|---|
| `status` | Show slot stats (cycles, successes, failures) |
| `pause` | Pause after the current lifecycle step |
| `resume` | Resume paused bots |
| `stop` | Graceful shutdown |
| Ctrl-C | Immediate (graceful) shutdown |

---

## AKMVyron commands

These are detected in-game chat / whispers from `TP_PLAYER`:

| Message | Effect |
|---|---|
| `/tpa <bot_name>` | Bot sends `/tpaccept` |
| `leave` (whisper / PM) | Bot disconnects immediately |

---

## Architecture

```
main.py          — entry point, asyncio.run(), console loop
bot_manager.py   — slot-based infinite retry loop, pause/resume/stop
minecraft_bot.py — state machine (IDLE→CONNECTING→…→DONE)
protocol.py      — async pyCraft wrapper (connect, send packets, recv events)
rotation.py      — 60 fps yaw rotation via PlayerLookPacket
vault.py         — right-click spam loop via PlayerBlockPlacementPacket
inventory.py     — OpenWindowPacket detection
config.py        — all settings with validation
utils.py         — username generator, RateThrottle, angle helpers
logger.py        — coloured ANSI logging with SUCCESS level
```

---

## Notes on pyCraft + Minecraft 1.21.4

pyCraft implements the Minecraft protocol in pure Python.  The library's
official release targets versions up to ~1.19; for 1.21.4, the core packet
structures (connect, chat, look, block-place, open-window) are unchanged, so
the bot operates correctly.  If your server runs an older version, adjust the
`VERSION` setting accordingly.

For offline-mode servers, Mojang authentication is skipped entirely —
the username is sent directly in the handshake.

# Minecraft Automation Bot

A production-ready Python 3.13+ bot that automates Ominous Vault cycling on your own Minecraft Java Edition server.  Built on the proven **Mineflayer** Node.js ecosystem, bridged into Python via the `javascript` package.

---

## What It Does

```
Generate random username
        ↓
Connect to server (offline mode)
        ↓
/register PASSWORD PASSWORD
        ↓
/login PASSWORD
        ↓
Wait for TP request from AKMVyron
        ↓
/tpaccept
        ↓
Wait 5 seconds
        ↓
Look straight → rotate 360° continuously
        ↓
Spam right-click vault (~8/s)
        ↓
Vault opens (window detected)
        ↓
Wait for rewards → close inventory
        ↓
Disconnect
        ↓
Repeat forever with a new username
```

---

## Requirements

| Tool | Version |
|---|---|
| Python | 3.13+ |
| Node.js | 18+ |
| npm | 8+ |

---

## Quick Start

### 1. Clone / copy the project

```bash
# All files are inside the minecraft_bot/ directory
cd minecraft_bot
```

### 2. Run the setup script (one-time)

```bash
chmod +x setup.sh
./setup.sh
```

This will:
- Verify Python 3.13+ and Node.js 18+
- `pip install -r requirements.txt`
- `npm install` (Mineflayer + dependencies)
- Copy `.env.example` → `.env`

### 3. Configure your server

**Option A – ip.txt (simplest)**

```
# ip.txt
play.myserver.net
```

**Option B – .env file**

```env
HOST=play.myserver.net
PORT=25565
VERSION=1.21.4
PASSWORD=YourBotPassword
TP_PLAYER=AKMVyron
RIGHT_CLICK_PER_SECOND=8
WAIT_AFTER_TP=5
```

See `.env.example` for all available options.

### 4. Launch

```bash
python3 main.py
```

---

## Manual Setup (without setup.sh)

```bash
# Python deps
pip3 install -r requirements.txt

# Node.js deps (Mineflayer)
npm install

# Copy config
cp .env.example .env
# Edit .env with your server details
```

---

## Console Commands

While the bot is running, type commands and press Enter:

| Command | Effect |
|---|---|
| `status` | Print cycle counts and current username per slot |
| `pause` | Pause all bots after the current lifecycle step |
| `resume` | Resume paused bots |
| `stop` | Gracefully shut down all bots and exit |

Press **Ctrl-C** for an immediate graceful shutdown.

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `HOST` | *(ip.txt)* | Server hostname or IP |
| `PORT` | `25565` | Server port |
| `VERSION` | `1.21.4` | Minecraft protocol version |
| `PASSWORD` | `Secure@Bot2025!` | `/register` and `/login` password |
| `TP_PLAYER` | `AKMVyron` | Player whose TP request is auto-accepted |
| `RIGHT_CLICK_PER_SECOND` | `8` | Vault right-click rate |
| `WAIT_AFTER_TP` | `5` | Seconds to wait after teleport |
| `ROTATION_SPEED` | `25` | Degrees/second for yaw rotation |
| `RECONNECT_DELAY` | `5` | Seconds between cycles |
| `MAX_BOTS` | `1` | Concurrent bot slots |
| `TP_WAIT_TIMEOUT` | `120` | Max seconds to wait for TP request |
| `VAULT_OPEN_TIMEOUT` | `30` | Max seconds to attempt vault opening |
| `INVENTORY_SETTLE_DELAY` | `2` | Seconds to wait for rewards before closing |

---

## Project Structure

```
minecraft_bot/
├── main.py           Entry point; console command loop
├── bot_manager.py    Orchestrates concurrent bot slots (pause/resume/stop)
├── minecraft_bot.py  Single bot lifecycle (connect → vault → disconnect)
├── rotation.py       Smooth 360° continuous yaw rotation (60 fps loop)
├── inventory.py      Detects vault window open; closes inventory
├── vault.py          Finds vault block; spams right-click
├── config.py         All configuration (env vars + ip.txt)
├── logger.py         Coloured timestamped logging
├── utils.py          Username generation, rate throttle, angle helpers
├── requirements.txt  Python dependencies
├── package.json      Node.js dependencies (Mineflayer)
├── .env.example      Config template
├── ip.txt            Put your server IP here
└── setup.sh          One-shot setup script
```

---

## Username Generation

Every cycle generates a fresh 6-character alphanumeric username:
- Characters: `A-Z`, `a-z`, `0-9`
- No symbols, no spaces
- Never reused within the same runtime session
- Examples: `Ab82Kd`, `Rx12Qa`, `Kd73Lp`

---

## Log Output Example

```
[12:31:52] [Manager] Starting 1 bot slot(s)…
[12:31:52] [Manager] 1 slot(s) running
[12:31:52] [Manager] Slot 0: Generated username: Ab82Kd
[12:31:52] [Bot]     [Ab82Kd] Connecting to play.myserver.net:25565 (v1.21.4)…
[12:31:53] [Bot]     [Ab82Kd] Connected
[12:31:53] [Bot]     [Ab82Kd] Spawned in world
[12:31:53] [Bot]     [Ab82Kd] Sending /register…
[12:31:55] [Bot]     [Ab82Kd] Registered (or already registered)
[12:31:55] [Bot]     [Ab82Kd] Sending /login…
[12:31:57] [Bot]     [Ab82Kd] Logged in
[12:31:57] [Bot]     [Ab82Kd] Waiting for TP from AKMVyron…
[12:32:04] [Bot]     [Ab82Kd] Sending /tpaccept…
[12:32:04] [Bot]     [Ab82Kd] TP accepted
[12:32:04] [Bot]     [Ab82Kd] Teleported – waiting 5s before interacting…
[12:32:09] [Bot]     [Ab82Kd] Starting vault interaction
[12:32:09] [Rotation] Rotation started at 25.0°/s
[12:32:09] [Vault]   Spamming right-click at 8/s (timeout=30s)…
[12:32:11] [Inventory] Window opened: type=vault …
[12:32:11] [Vault]   Vault opened successfully!
[12:32:13] [Inventory] Inventory closed
[12:32:13] [Bot]     [Ab82Kd] Disconnecting…
[12:32:14] [Bot]     [Ab82Kd] Disconnected
[12:32:14] [Manager] Slot 0: Cycle complete ✓  (total=1, ok=1)
[12:32:14] [Manager] Slot 0: Waiting 5s before next cycle…
```

---

## Error Recovery

The bot automatically recovers from:

| Error | Recovery |
|---|---|
| Kicked | Reconnects after `RECONNECT_DELAY` |
| Timeout | Reconnects after `RECONNECT_DELAY` |
| Connection dropped | Reconnects after `RECONNECT_DELAY` |
| Login failed | Ignored – continues to TP wait |
| Vault timeout | Disconnects, starts new cycle |
| Inventory error | Logged and skipped |
| Packet error | Logged; bot moves to next step |
| Any unhandled exception | Caught at slot level; new cycle starts |

The process **never crashes** – every exception is caught at the slot loop level.

---

## Running Multiple Bots

Set `MAX_BOTS=3` in `.env` to run 3 concurrent slots:

```env
MAX_BOTS=3
```

Each slot uses its own randomly generated username and independent lifecycle.  All slots share a single event loop for low CPU overhead.

---

## Notes

- This bot is designed for **offline-mode servers only** (no Mojang/Microsoft authentication).
- The bot joins with `auth: "offline"` – it does not need a valid Minecraft account.
- Mineflayer supports Minecraft versions 1.8 through 1.21.x – set `VERSION` to match your server exactly.
- The vault block detection uses `bot.findBlock` with a 5-block radius.  If the bot is teleported directly next to the vault, it will be found automatically.
- If the server uses a different registration plugin command format, edit `minecraft_bot.py` → `_register` and `_login` methods.

# ⚔️ Thorpedo — Valheim Dedicated Server & Control Panel Stack

[![Docker](https://img.shields.io/badge/Docker-All--in--One-blue.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![Valheim](https://img.shields.io/badge/Valheim-1.0+-red.svg?logo=steam&logoColor=white)](https://store.steampowered.com/app/892970/Valheim/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Supervisor%20Backend-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Crossplay](https://img.shields.io/badge/PlayFab-Crossplay%20Ready-0078D4.svg?logo=microsoftazure&logoColor=white)](https://playfab.com/)
[![License](https://img.shields.io/badge/License-MIT-amber.svg)](LICENSE)

A production-grade, containerized **All-in-One Valheim Dedicated Server Stack** featuring an integrated Web Management Control Panel (**Thorpedo Control UI**). Built for server admins who want complete control over their Valheim server without touching SSH or terminal commands for daily operations.

Engineered with an **in-container Python process supervisor**, in-place anti-hang restart mechanics (no `docker restart` required), live PlayFab Join Code rotation, multi-language UI (🇬🇧 English / 🇮🇩 Indonesian), drag-and-drop mod manager, one-click world migration, automated hourly backups with retention policies, Discord multi-webhook alerting with role mentions, and optional GitHub Releases offsite sync.

---

## ⚡ Quickstart: Fork & Run in 3 Minutes ("Sat-Set" Setup)

Ready to deploy your own dedicated Valheim server immediately? Follow these simple steps:

### 1. Fork this Repository
Click the **Fork** button at the top right of this GitHub page to copy this repository to your account.

### 2. Clone to Your Server / VPS
```bash
git clone https://github.com/<YOUR-USERNAME>/Thorpedo-Valheim.git
cd Thorpedo-Valheim
```

### 3. Initialize Environment
Copy the example configuration:
```bash
cp .env.example .env
```
*(Optional)* Edit `.env` to set your custom `SERVER_NAME`, `SERVER_PASS`, and admin panel credentials:
```bash
nano .env
```

### 4. Build and Start the Stack
```bash
docker compose build
docker compose up -d
```

### 5. Access Your Web Control Panel
Open your browser and navigate to:
```
http://<YOUR_SERVER_IP>:28456
```
- **Default Username**: `admin`
- **Default Password**: `ThorpedoAdmin2026!` *(Changeable in UI Settings)*

> **Tip for Public / Domain Access**: Expose port `28456` in seconds using Cloudflare Tunnel without opening router ports:
> ```bash
> cloudflared tunnel --url http://127.0.0.1:28456
> ```

---

## 🏛️ System Architecture

```
                 [ Steam / Game Pass / Community Players ]
                                     │
                                     ▼
                         Microsoft PlayFab Relay
                           (6-Digit Join Code)
                                     │
                                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Host Server (Linux / Docker Host)                                    │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ Container: thorpedo-valheim (All-in-One Container)             │  │
│  │                                                                │  │
│  │  ┌──────────────────────────────┐   ┌───────────────────────┐  │  │
│  │  │ Web Control Panel            │   │ Valheim Game Engine   │  │  │
│  │  │ • FastAPI + Uvicorn          │   │ • Valheim 1.0+ Engine │  │  │
│  │  │ • Port: 28456 (HTTP)         │   │ • BepInEx 5.4 Loader  │  │  │
│  │  │ • In-Memory Ring Buffer      │   │ • Port: 2456-2457/udp │  │  │
│  │  │ • Process Supervisor:        │──►│ • Managed Subprocess  │  │  │
│  │  │   - Graceful flush (SIGINT)  │   │   (In-place restart)  │  │  │
│  │  │   - Emergency kill (SIGKILL) │   └───────────────────────┘  │  │
│  │  └──────────────────────────────┘                              │  │
│  └─────────────────┬──────────────────────────────┬───────────────┘  │
│                    │                              │                  │
│  ┌─────────────────▼──────────────┐ ┌─────────────▼───────────────┐  │
│  │ Persistent Bind Mounts         │ │ Edge Reverse Tunnel         │  │
│  │ • ./data -> /config            │ │ • Cloudflare Tunnel         │  │
│  │ • ./.env -> /config/.env       │ │ • Remote Web HTTPS Access   │  │
│  │ • ./backups -> /config/backups │ │ • Zero Open Panel Ports     │  │
│  │ • thorpedo-data -> /valheim    │ └─────────────────────────────┘  │
│  └────────────────────────────────┘                                  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## ✨ Feature Highlights

### 🛡️ 1. In-Place Anti-Hang Process Supervisor
- **No Container Downtime**: The FastAPI panel acts as an internal daemon supervisor over `valheim_server.x86_64`.
- **Graceful Restart (`SIGINT`)**: Notifies all connected players, flushes all world terrain chunks and character data cleanly to disk, and respawns the game server in under 5 seconds.
- **Force Kill (`SIGKILL / kill -9`)**: An emergency button that kills a frozen engine instantly if socket threads freeze or hang, reviving the world immediately without restarting Docker.
- **PlayFab Socket Refresh**: Reopens Azure matchmaking registration to generate fresh 6-digit Join Codes on demand.

### 🌐 2. Modern Tactical UI & Language Switcher
- Dark, clean tactical interface optimized for system admins and community moderators.
- **Bilingual Internationalization (I18N)**: Instant one-click language toggle at the bottom-left sidebar between **🇬🇧 English** and **🇮🇩 Indonesia** (saved in `localStorage`).
- Real-time live status polling (CPU %, RAM MB, Free Disk GB, active PlayFab Join Code, Direct IP, and online player roster).
- **Server Password Quick-View**: Easily view, hide/reveal (`••••••••`), and copy the server password directly from the Overview card.

### 🧩 3. Plugin & Mod Manager (BepInEx)
- **Upload Mods**: Upload `.dll` single plugins or `.zip` mod bundles directly through the browser.
- **Toggle On/Off**: Enable or disable plugins on the fly (renames to `.dll.disabled`).
- **Delete Plugins**: Permanently delete unused mods from the UI with safety confirmation.
- **Pre-installed Server-Side Plugins**:
  - `NoCheatFlag.dll`: Prevents false-positive cheat flag infections.
  - `Gjallarhorn.dll`: Nordic death announcements and boss banner notifications.
  - `CrossplayJoinCodeFix.dll`: Ensures 100% reliable PlayFab Join Code resolution.
  - `WorkbenchLove.dll`: Expands workbench build radius from 20m to 40m.
  - `ServerDevcommands.dll`: Unlocks multiplayer admin commands.
  - `ScheduledMessages.dll`: Auto-broadcasts server announcements and countdowns.
  - `NetworkPerformanceSystem.dll`: Optimizes UDP network throughput and packet windows.

### 💾 4. World Importer & Backup Engine
- **Migrate Single-Player World**: Click **Upload World Save** in the Snapshots tab to drop your local PC `.db` and `.fwl` files directly into the server.
- **Customizable Auto-Backup**: Configure automated snapshots every 2, 6, 12, or 24 hours.
- **Local Retention Policy**: Prevent host disk full errors by setting automatic pruning (keep 5, 10, 25, or 50 latest backups).
- **One-Click Download & Rollback**: Download snapshot `.zip` archives directly to your PC, or perform an instant rollback with automatic pre-rollback safety snapshots.

### 📢 5. Discord Multi-Webhook Sentinel
- Set up unlimited Discord webhooks directly from the **Settings** tab.
- Role Mention support: Ping `@everyone` or specific role IDs (`<@&123456789>`).
- Custom accent embed colors (HEX).
- Per-webhook event triggers: *Server Online*, *Join Code Rotated*, *Player Events*, and *Backup Completed*.
- Live webhook testing button with immediate feedback.

### ☁️ 6. GitHub Releases Offsite Backup
- Connect your GitHub repository and Personal Access Token (PAT) via the Settings tab.
- Automatically pushes world snapshot archives to GitHub Releases for offsite disaster recovery.

---

## 🎮 How Players Connect (Client Guide)

### 1. Official Steam Players
1. Launch Valheim via Steam.
2. Click **Join Game** → select **Add Server**.
3. Enter the **6-digit Join Code** (e.g., `795624`) displayed on the Thorpedo Control Panel.
4. Enter the server password when prompted.

### 2. Non-Steam / Community / Spacewar (AppID 480) Players
1. Ensure your Steam client is **running in the background** (any free account).
2. Launch the game through your launcher/fix (Steam overlay will indicate playing *Spacewar*).
3. In the Valheim main menu: click **Join Game** → click **Add Server** *(Do NOT use Join IP directly)*.
4. Input the active **6-digit Join Code** and server password.

---

## 📁 Repository Directory Layout

```
.
├── Dockerfile                    # Multi-stage image: Valheim Dedicated + Python 3.11 + FastAPI
├── compose.yaml                  # Unified Docker Compose All-in-One service definition
├── entrypoint_allinone.sh        # Bootstrapper for SteamCMD, BepInEx loader, and Uvicorn panel
├── .env.example                  # Environment template with recommended defaults
├── panel/
│   ├── main.py                   # FastAPI application, REST endpoints, and file upload handlers
│   ├── engine_manager.py         # Subprocess supervisor (SIGINT, SIGKILL, log stream parser)
│   ├── services.py               # World management, snapshot archive, and mod controller
│   ├── settings_manager.py       # Persistent JSON settings layer for webhooks and backups
│   ├── auth.py                   # PBKDF2 HMAC password security & session token verification
│   ├── templates/
│   │   ├── index.html            # Responsive tactical Web UI with multi-language engine
│   │   └── login.html            # Secure authentication portal
│   └── tests/
│       ├── test_engine_manager.py# Supervisor unit tests
│       └── test_api.py           # REST API & authentication test suite (16 tests)
├── data/                         # Bind-mounted world save files, adminlist, and bannedlist
├── plugins/                      # Bind-mounted BepInEx plugins (.dll)
└── README.md
```

---

## 🔧 REST API Endpoints Overview

All management routes are protected via authenticated session cookies (`thorpedo_session`):

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | `GET` | Returns live server vitals, CPU %, RAM MB, Join Code, and players |
| `/api/actions/execute` | `POST` | Executes `restart_graceful`, `restart_force`, `force_save`, `reset_playfab` |
| `/api/console/command` | `POST` | Dispatches admin console command to server `stdin` |
| `/api/logs` | `GET` | Fetches parsed log ring-buffer (filterable by category) |
| `/api/server/config` | `POST` | Updates server name, password, and auto-backup schedules |
| `/api/modifiers` | `GET` / `POST` | Reads and updates world gameplay modifiers in `.env` |
| `/api/snapshots` | `GET` | Lists local and GitHub Release world snapshot archives |
| `/api/snapshots/create` | `POST` | Flushes world chunks and creates a timestamped `.zip` archive |
| `/api/snapshots/rollback` | `POST` | Stops server, extracts target snapshot, and restarts game |
| `/api/snapshots/upload-world` | `POST` | Multipart upload for local `.db` and `.fwl` world migration |
| `/api/mods` | `GET` | Returns installed BepInEx plugins and active state |
| `/api/mods/toggle` | `POST` | Enables or disables target plugin (`.dll` ⟷ `.dll.disabled`) |
| `/api/mods/upload` | `POST` | Uploads `.dll` plugin or `.zip` mod archive directly to plugins |
| `/api/mods/{name}` | `DELETE` | Deletes plugin file from the server |
| `/api/players` | `GET` | Retrieves `adminlist.txt` and `bannedlist.txt` Steam IDs |
| `/api/players/admin` | `POST`/`DELETE` | Adds or removes an admin Steam ID |
| `/api/players/ban` | `POST`/`DELETE` | Adds or removes a banned Steam ID |
| `/api/settings` | `GET` / `POST` | Manages webhooks, GitHub tokens, and system settings |
| `/api/settings/discord/test` | `POST` | Sends live test message to specified Discord webhook URL |
| `/api/settings/github/test` | `POST` | Verifies repository access with provided GitHub PAT |

---

## 🤝 Contributing

Contributions, bug reports, and pull requests are warmly welcomed!
1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Run test suite: `pytest panel/tests/ -v`
4. Commit your Changes (`git commit -m 'feat: Add AmazingFeature'`)
5. Push to the Branch (`git push origin feature/AmazingFeature`)
6. Open a Pull Request

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.

*Built with passion for the Valheim community by [Thaaan](https://github.com/Thaaan184).*

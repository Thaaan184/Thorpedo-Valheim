# Thorpedo Valheim All-in-One Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy an All-in-One Docker container for Thorpedo on server `ids` that co-hosts the Valheim dedicated server and the web management panel (port 28456, Cloudflare Tunnel ready), with an internal Python process supervisor enabling in-place graceful and force restarts without stopping the Docker container.

**Architecture:** Single container extending `ghcr.io/reloadlife/valheim-server:latest` with Python 3, FastAPI, Uvicorn, and psutil. The FastAPI panel serves the user-designed `template.html` and acts as a process supervisor for `valheim_server.x86_64`, handling real-time stdout ring-buffering, graceful `SIGINT` chunk-flush restarts, instant `SIGKILL` hang recovery, PlayFab lobby resets, and world snapshot management.

**Tech Stack:** Docker, Docker Compose, Linux Bash, Python 3, FastAPI, Uvicorn, Jinja2, psutil, BepInEx, Valheim Dedicated Server.

**Spec:** User request: unified single container, hosting on server `ids`, cleanup on VM LLM, preserved world save (`BarudakValheim`), UI based on `template.html`, robust restart for server hang recovery.

## Global Constraints

- Never lose or overwrite existing world save data (`BarudakValheim` at `_main.367.db2`).
- Valheim game ports remain `2456-2457/udp`.
- Web management panel listens on port `28456/tcp` (high port, ready for Cloudflare Tunnel forward).
- Restart operations (graceful, force, reset) must execute strictly on the internal game process without killing or recreating the Docker container.
- Graceful shutdown/restart must issue `SIGINT` and allow dirty chunks to flush before termination.

## Review Focus

1. **Process Hang Recovery**: When `valheim_server.x86_64` stops responding to `SIGINT`, `restart_force` immediately sends `SIGKILL` (`kill -9`) to guarantee unblocked process recreation.
2. **Chunk Flush Guarantee**: Graceful restart waits up to 30s for the game process to exit after `SIGINT` before restarting.
3. **Log Stream Ring-Buffer**: Engine stdout/stderr is continuously read in a background daemon thread into a 500-line ring-buffer so Console never blocks or leaks memory.
4. **World Metadata Preservation**: Container startup preserves existing `.fwl` and `.db2` files without overwriting seed or world generation versions.
5. **Data Volume Mounts**: Host `./data` is mapped to `/config` and `./plugins` to BepInEx plugins directory.

---

### Task 1: Migrate and Verify World Save & Configuration on IDS Host

**Files:**
- Create: `/home/ids/projects/Thorpedo-Valheim/.env`
- Copy: `/home/ids/projects/Thorpedo-Valheim/data/`
- Copy: `/home/ids/projects/Thorpedo-Valheim/plugins/`

**Interfaces:**
- Consumes: `/home/ids/thorpedo_world_backup/`
- Produces: Ready-to-mount `./data`, `./plugins`, and `./.env` in `/home/ids/projects/Thorpedo-Valheim/`

- [ ] **Step 1: Copy backup files to Thorpedo-Valheim workspace**
Copy active world save, plugins, and `.env` from `/home/ids/thorpedo_world_backup/` to `/home/ids/projects/Thorpedo-Valheim/`.
- [ ] **Step 2: Verify world save integrity**
Inspect `data/worlds_local/BarudakValheim/` to confirm `_main.367.db2`, `_main.367.fwl2`, `.chunks`, and all `.chunk` files exist and are non-empty.
- [ ] **Step 3: Update `.env` with PANEL_PORT=28456**
Ensure `.env` contains `PANEL_PORT=28456` and all Valheim server parameters.
- [ ] **Step 4: Commit**
```bash
git add .env.example
git commit -m "chore: prepare workspace data and configuration for all-in-one container"
```

---

### Task 2: Build the Process Supervisor Engine (`engine_manager.py`)

**Files:**
- Create: `/home/ids/projects/Thorpedo-Valheim/panel/engine_manager.py`
- Test: `/home/ids/projects/Thorpedo-Valheim/panel/tests/test_engine_manager.py`

**Interfaces:**
- Produces: `GameServerManager` singleton with methods:
  - `start() -> bool`
  - `stop(timeout: int = 30) -> bool`
  - `restart(graceful: bool = True, delay_seconds: int = 0) -> bool`
  - `force_kill() -> bool`
  - `get_status() -> dict`
  - `get_logs(filter_type: Optional[str] = None, count: Optional[int] = None) -> list`
  - `send_admin_command(command: str) -> bool`

- [ ] **Step 1: Write unit tests for `GameServerManager`**
Test state transitions (`stopped` -> `running` -> `rebooting`), graceful SIGINT termination, SIGKILL fallback, and log ring-buffer extraction.
- [ ] **Step 2: Run test to verify it fails**
`python3 -m pytest panel/tests/test_engine_manager.py -v`
- [ ] **Step 3: Implement `GameServerManager` in `panel/engine_manager.py`**
Implement thread-safe process execution, stdout line parsing (classifying `INFO`, `CONN`, `DEATH`, `WARN`, `ERROR`), PlayFab join code regex extraction (`with join code (\d+) and IP .* is active`), and signal management.
- [ ] **Step 4: Run test to verify it passes**
`python3 -m pytest panel/tests/test_engine_manager.py -v`
- [ ] **Step 5: Commit**
```bash
git add panel/engine_manager.py panel/tests/test_engine_manager.py
git commit -m "feat(panel): implement in-container game server process supervisor"
```

---

### Task 3: Implement API Endpoints & Integrate `template.html`

**Files:**
- Create: `/home/ids/projects/Thorpedo-Valheim/panel/templates/index.html` (from `template.html`)
- Modify: `/home/ids/projects/Thorpedo-Valheim/panel/main.py`
- Modify: `/home/ids/projects/Thorpedo-Valheim/panel/services.py`
- Test: `/home/ids/projects/Thorpedo-Valheim/panel/tests/test_api.py`

**Interfaces:**
- Consumes: `GameServerManager` from Task 2
- Produces:
  - `GET /` -> renders `index.html`
  - `GET /api/status` -> JSON status payload matching `template.html` format
  - `POST /api/actions/execute` -> executes `restart_graceful`, `restart_force`, `start`, `stop`, `force_save`, `reset_playfab`, `broadcast_discord`, `check_playfab`
  - `GET /api/snapshots` & `POST /api/snapshots/rollback`
  - `GET/POST /api/players/admins`, `GET/POST /api/players/bans`
  - `GET/POST /api/world/modifiers`

- [ ] **Step 1: Install `template.html` as `panel/templates/index.html`**
Copy `/home/ids/.hermes/cache/documents/doc_4841400fc0b4_template.html` into `panel/templates/index.html`.
- [ ] **Step 2: Write API endpoint integration tests**
Test `/api/status`, `/api/actions/execute`, and template rendering.
- [ ] **Step 3: Implement endpoint handlers in `main.py` and `services.py`**
Wire API endpoints directly to `GameServerManager` and filesystem configs (`adminlist.txt`, `bannedlist.txt`, `.env`).
- [ ] **Step 4: Run test to verify it passes**
`python3 -m pytest panel/tests/test_api.py -v`
- [ ] **Step 5: Commit**
```bash
git add panel/main.py panel/services.py panel/templates/index.html panel/tests/test_api.py
git commit -m "feat(panel): integrate new design template and bind supervisor REST API"
```

---

### Task 4: Dockerize All-in-One Container (`Dockerfile` & `compose.yaml`)

**Files:**
- Create: `/home/ids/projects/Thorpedo-Valheim/Dockerfile`
- Create: `/home/ids/projects/Thorpedo-Valheim/entrypoint_allinone.sh`
- Modify: `/home/ids/projects/Thorpedo-Valheim/compose.yaml`

**Interfaces:**
- Consumes: `panel/` and base image `ghcr.io/reloadlife/valheim-server:latest`
- Produces: Self-contained image and Docker Compose deployment.

- [ ] **Step 1: Write `Dockerfile`**
Base on `ghcr.io/reloadlife/valheim-server:latest`, install Python 3, venv, required packages (`fastapi`, `uvicorn`, `jinja2`, `psutil`, `requests`, `python-dotenv`), and copy `panel/` into `/panel`.
- [ ] **Step 2: Write `entrypoint_allinone.sh`**
Initialize environment, write `.fwl` metadata if first run, start background FastAPI Uvicorn on `0.0.0.0:28456`, and trigger game auto-boot via supervisor.
- [ ] **Step 3: Update `compose.yaml`**
Configure service `thorpedo` with `build: .`, port mappings `28456:28456` and `2456-2457:2456-2457/udp`, volumes `./data:/config`, `./plugins:/opt/bepinex/BepInEx/plugins:ro`, and `./backups:/opt/thorpedo/backups`.
- [ ] **Step 4: Commit**
```bash
git add Dockerfile entrypoint_allinone.sh compose.yaml
git commit -m "feat(docker): create all-in-one Dockerfile and compose configuration"
```

---

### Task 5: Deploy, Validate In-Place Restart, and Git Push

**Files:**
- Deployment runtime on server `ids`

- [ ] **Step 1: Build Docker image locally**
Run `docker compose -f compose.yaml build` on server `ids`.
- [ ] **Step 2: Start All-in-One container**
Run `docker compose -f compose.yaml up -d` on server `ids`.
- [ ] **Step 3: Verify Panel and Valheim Engine**
Check `curl -s http://127.0.0.1:28456/api/status`. Confirm server reports `running`, memory usage, world loaded (`BarudakValheim`), and PlayFab status.
- [ ] **Step 4: Validate In-Place Restart via Panel API**
Issue `POST /api/actions/execute` with `{"action": "restart_graceful", "delay_seconds": 0}`.
Verify container remains `Up` (`docker ps`), game PID cycles cleanly, and world saves flush without error.
- [ ] **Step 5: Git Push to GitHub**
Push all commits to `Thaaan184/Thorpedo-Valheim`.

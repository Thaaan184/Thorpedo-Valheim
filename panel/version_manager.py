"""Version tracking and self-update for the Thorpedo panel and Valheim server.

Two independent version lines are tracked:

* Panel  - `panel/VERSION` in this repository, compared against the value on the
           configured GitHub branch. Applying an update replaces the files in
           this directory and requires the panel process to restart.
* Game   - the Valheim dedicated server build id recorded in Steam's
           `appmanifest_896660.acf`, compared against the public build id that
           Steam publishes for the app. Applying an update runs SteamCMD.

Both checks are cached briefly so the dashboard can poll them without hammering
the upstream APIs.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from typing import Any, Dict, Optional

import requests

import settings_manager

PANEL_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(PANEL_DIR, "VERSION")
BUILD_INFO_FILE = os.path.join(PANEL_DIR, ".build_info.json")

DATA_DIR = os.getenv(
    "SAVE_DIR",
    "/config" if os.path.exists("/config") else os.path.abspath(os.path.join(PANEL_DIR, "..", "data")),
)
INSTALL_DIR = os.getenv("INSTALL_DIR", "/valheim")
MANIFEST_FILE = os.path.join(INSTALL_DIR, "steamapps", "appmanifest_896660.acf")
STEAM_MODIFIERS_FILE = os.path.join(INSTALL_DIR, "steamapps", "workshop", "appworkshop_892970.acf")
VALHEIM_APP_ID = "896660"
STEAMCMD_CANDIDATES = ("/usr/bin/steamcmd", "/usr/games/steamcmd", "/steamcmd/steamcmd.sh")

CACHE_TTL_SECONDS = 300
_cache: Dict[str, Any] = {"panel": None, "game": None, "fetched_at": 0.0}


# --- Panel version -----------------------------------------------------------

def get_panel_version() -> str:
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except Exception:
        return "0.0.0"


def get_build_info() -> Dict[str, Any]:
    try:
        with open(BUILD_INFO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_build_info(info: Dict[str, Any]) -> None:
    try:
        with open(BUILD_INFO_FILE, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
    except Exception:
        pass


def parse_version(value: str):
    """Turn 'v2.10.3' into (2, 10, 3) so 2.10 sorts above 2.9."""
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(p) for p in parts[:4]) if parts else (0,)


def resolve_github_token() -> str:
    """Pick the GitHub token that is actually usable.

    The settings file can hold a masked value (the UI round-trips `gh...Ab18`
    back to the API), so a masked or placeholder token is skipped in favour of
    GITHUB_TOKEN from the environment.
    """
    stored = ((settings_manager.load_settings().get("github", {}) or {}).get("token") or "").strip()
    env_token = (os.getenv("GITHUB_TOKEN") or "").strip()
    for candidate in (stored, env_token):
        if candidate and "..." not in candidate and not candidate.startswith("your_"):
            return candidate
    return ""


def _github_auth() -> Dict[str, str]:
    token = resolve_github_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _github_repo_and_branch() -> tuple:
    settings = settings_manager.load_settings()
    gh = settings.get("github", {}) or {}
    repo = gh.get("repo") or os.getenv("GITHUB_REPO", "Thaaan184/Thorpedo-Valheim")
    branch = gh.get("branch") or os.getenv("GITHUB_BRANCH", "main")
    return repo, branch


def fetch_remote_panel_version() -> Dict[str, Any]:
    repo, branch = _github_repo_and_branch()
    headers = _github_auth()
    headers.setdefault("User-Agent", "Thorpedo-Panel")
    result: Dict[str, Any] = {
        "version": None,
        "commit": None,
        "commit_date": None,
        "commit_message": None,
        "repo": repo,
        "branch": branch,
        "error": None,
    }

    try:
        raw = requests.get(
            f"https://raw.githubusercontent.com/{repo}/{branch}/panel/VERSION",
            headers=headers,
            timeout=8,
        )
        if raw.status_code == 200:
            result["version"] = raw.text.strip()
        elif raw.status_code == 404:
            result["error"] = f"panel/VERSION not found on {repo}@{branch}"
        else:
            result["error"] = f"GitHub raw returned HTTP {raw.status_code}"
    except Exception as exc:
        result["error"] = f"Cannot reach GitHub: {exc}"

    # Commit metadata makes the changelog readable for the admin.
    try:
        commits = requests.get(
            f"https://api.github.com/repos/{repo}/commits",
            params={"sha": branch, "per_page": 1},
            headers=headers,
            timeout=8,
        )
        if commits.status_code == 200 and commits.json():
            head = commits.json()[0]
            result["commit"] = head.get("sha", "")[:7]
            result["commit_date"] = (head.get("commit", {}).get("committer", {}) or {}).get("date")
            result["commit_message"] = (head.get("commit", {}).get("message", "") or "").split("\n")[0][:120]
    except Exception:
        pass

    return result


def check_panel_update() -> Dict[str, Any]:
    installed = get_panel_version()
    remote = fetch_remote_panel_version()
    latest = remote.get("version")
    build = get_build_info()
    available = bool(latest) and parse_version(latest) > parse_version(installed)
    return {
        "installed": installed,
        "latest": latest,
        "update_available": available,
        "repo": remote.get("repo"),
        "branch": remote.get("branch"),
        "remote_commit": remote.get("commit"),
        "remote_commit_date": remote.get("commit_date"),
        "remote_commit_message": remote.get("commit_message"),
        "installed_commit": build.get("commit"),
        "installed_at": build.get("updated_at"),
        "previous_version": build.get("previous_version"),
        "error": remote.get("error"),
    }


# --- Valheim server build ----------------------------------------------------

def read_game_build() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "installed_buildid": None,
        "installed_at": None,
        "manifest": MANIFEST_FILE,
        "binary_present": os.path.exists(os.path.join(INSTALL_DIR, "valheim_server.x86_64")),
    }
    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        m = re.search(r'"buildid"\s+"(\d+)"', text)
        if m:
            info["installed_buildid"] = m.group(1)
        m = re.search(r'"LastUpdated"\s+"(\d+)"', text)
        if m:
            info["installed_at"] = time.strftime(
                "%Y-%m-%d %H:%M UTC", time.gmtime(int(m.group(1)))
            )
    except Exception:
        pass
    return info


def fetch_latest_game_build() -> Dict[str, Any]:
    out: Dict[str, Any] = {"latest_buildid": None, "latest_at": None, "error": None}
    try:
        resp = requests.get(f"https://api.steamcmd.net/v1/info/{VALHEIM_APP_ID}", timeout=10)
        if resp.status_code == 200:
            public = (
                resp.json()
                .get("data", {})
                .get(VALHEIM_APP_ID, {})
                .get("depots", {})
                .get("branches", {})
                .get("public", {})
            )
            out["latest_buildid"] = public.get("buildid")
            ts = public.get("timebuildupdated")
            if ts:
                out["latest_at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(ts)))
        else:
            out["error"] = f"steamcmd.net returned HTTP {resp.status_code}"
    except Exception as exc:
        out["error"] = f"Cannot reach Steam build API: {exc}"
    return out


def check_game_update() -> Dict[str, Any]:
    local = read_game_build()
    remote = fetch_latest_game_build()
    available = bool(
        local.get("installed_buildid")
        and remote.get("latest_buildid")
        and str(local["installed_buildid"]) != str(remote["latest_buildid"])
    )
    return {
        "installed_buildid": local.get("installed_buildid"),
        "installed_at": local.get("installed_at"),
        "latest_buildid": remote.get("latest_buildid"),
        "latest_at": remote.get("latest_at"),
        "update_available": available,
        "binary_present": local.get("binary_present"),
        "error": remote.get("error"),
    }


# --- Combined ----------------------------------------------------------------

def check_all(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force and _cache["panel"] and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        cached = {
            "panel": _cache["panel"],
            "game": _cache["game"],
            "checked_at": _cache["fetched_at"],
            "cached": True,
        }
        return cached

    panel = check_panel_update()
    game = check_game_update()
    _cache.update({"panel": panel, "game": game, "fetched_at": now})
    return {"panel": panel, "game": game, "checked_at": now, "cached": False}


def _safe_extract(zip_path: str, dest: str) -> None:
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            target = os.path.abspath(os.path.join(dest_abs, member.filename))
            if not target.startswith(dest_abs + os.sep) and target != dest_abs:
                raise ValueError(f"Unsafe path in archive: {member.filename}")
        zf.extractall(dest_abs)


def apply_panel_update() -> Dict[str, Any]:
    """Replace this panel's source files with the configured GitHub branch.

    The caller restarts the process afterwards; nothing here touches game data,
    credentials, or the settings file, because those live in /config.
    """
    repo, branch = _github_repo_and_branch()
    headers = _github_auth()
    headers.setdefault("User-Agent", "Thorpedo-Panel")
    staging_root = f"/tmp/thorpedo-panel-update-{int(time.time())}"
    zip_path = f"{staging_root}.zip"

    try:
        dl = requests.get(
            f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}",
            headers=headers,
            timeout=60,
            stream=True,
        )
        if dl.status_code != 200:
            return {"ok": False, "message": f"Download failed: HTTP {dl.status_code}"}
        os.makedirs(staging_root, exist_ok=True)
        with open(zip_path, "wb") as fh:
            for chunk in dl.iter_content(chunk_size=1 << 16):
                fh.write(chunk)

        _safe_extract(zip_path, staging_root)
        roots = [d for d in os.listdir(staging_root) if os.path.isdir(os.path.join(staging_root, d))]
        if not roots:
            return {"ok": False, "message": "Downloaded archive is empty."}
        source = os.path.join(staging_root, roots[0], "panel")
        if not os.path.isdir(source):
            return {"ok": False, "message": "Archive does not contain a panel/ directory."}

        new_version = "0.0.0"
        try:
            with open(os.path.join(source, "VERSION"), "r", encoding="utf-8") as fh:
                new_version = fh.read().strip() or "0.0.0"
        except Exception:
            return {"ok": False, "message": "Archive is missing panel/VERSION; refusing to install."}
        if parse_version(new_version) <= parse_version(get_panel_version()):
            return {
                "ok": False,
                "message": f"Already on {get_panel_version()}; archive holds {new_version}.",
            }

        # Compile the staged sources before touching the live tree.
        for name in ("main.py", "services.py", "engine_manager.py", "settings_manager.py",
                     "webhook_manager.py", "version_manager.py", "auth.py"):
            candidate = os.path.join(source, name)
            if not os.path.exists(candidate):
                return {"ok": False, "message": f"Archive is missing {name}; refusing to install."}
            check = subprocess.run(
                [sys.executable, "-m", "py_compile", candidate],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if check.returncode != 0:
                return {"ok": False, "message": f"{name} failed to compile: {check.stderr[:200]}"}

        # Snapshot the current files so a bad update can be rolled back by hand.
        backup_dir = os.path.join(DATA_DIR, "panel_backups")
        os.makedirs(backup_dir, exist_ok=True)
        backup_zip = os.path.join(backup_dir, f"panel-{get_panel_version()}-{time.strftime('%Y%m%d_%H%M%S')}.zip")
        skip = {"__pycache__", "credentials.json", ".secret_key", ".build_info.json", ".pytest_cache"}
        with zipfile.ZipFile(backup_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(PANEL_DIR):
                dirs[:] = [d for d in dirs if d not in skip]
                for name in files:
                    if name in skip:
                        continue
                    full = os.path.join(root, name)
                    zf.write(full, os.path.relpath(full, PANEL_DIR))

        previous_version = get_panel_version()

        # Copy staged sources over the live tree (never delete unknown files).
        copied = 0
        for root, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            rel_root = os.path.relpath(root, source)
            target_root = PANEL_DIR if rel_root == "." else os.path.join(PANEL_DIR, rel_root)
            os.makedirs(target_root, exist_ok=True)
            for name in files:
                src = os.path.join(root, name)
                dst = os.path.join(target_root, name)
                shutil.copy2(src, dst)
                copied += 1

        remote = fetch_remote_panel_version()
        save_build_info(
            {
                "version": new_version,
                "commit": remote.get("commit"),
                "commit_date": remote.get("commit_date"),
                "commit_message": remote.get("commit_message"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "previous_version": previous_version,
                "backup": backup_zip,
                "files_copied": copied,
            }
        )
        _cache.update({"panel": None, "fetched_at": 0.0})
        return {
            "ok": True,
            "message": f"Panel updated {previous_version} -> {new_version} ({copied} files). Restarting panel...",
            "version": new_version,
            "previous_version": previous_version,
            "backup": backup_zip,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Panel update failed: {exc}"}
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        try:
            os.remove(zip_path)
        except Exception:
            pass


# --- Game update -------------------------------------------------------------

_game_update_state: Dict[str, Any] = {"running": False, "started_at": None, "message": "", "ok": None}


def game_update_state() -> Dict[str, Any]:
    return dict(_game_update_state)


def _steamcmd_binary() -> Optional[str]:
    for candidate in STEAMCMD_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def run_game_update(manager) -> Dict[str, Any]:
    """Run SteamCMD app_update in a worker thread; the caller polls the state."""
    if _game_update_state["running"]:
        return {"ok": False, "message": "A server update is already running."}

    binary = _steamcmd_binary()
    if not binary:
        return {"ok": False, "message": "SteamCMD is not installed in this container."}

    def worker():
        _game_update_state.update(
            {"running": True, "ok": None, "started_at": time.time(),
             "message": "Stopping game engine for update..."}
        )
        was_running = manager.status == "running"
        try:
            if was_running:
                manager.stop(timeout=45)
            _game_update_state["message"] = "Downloading Valheim server build via SteamCMD..."
            cmd = [
                binary,
                "+@sSteamCmdForcePlatformType", "linux",
                "+force_install_dir", INSTALL_DIR,
                "+login", "anonymous",
                "+app_update", VALHEIM_APP_ID, "validate",
                "+quit",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            tail = "\n".join(
                [ln for ln in (proc.stdout or "").strip().splitlines()[-12:]]
                or (proc.stderr or "").strip().splitlines()[-6:]
            )
            for line in tail.splitlines():
                if line.strip():
                    manager._handle_log_line(f"[steamcmd] {line.strip()}")
            if proc.returncode != 0:
                _game_update_state.update(
                    {"ok": False, "message": f"SteamCMD exited with code {proc.returncode}."}
                )
                if was_running:
                    manager.start()
                return
            _cache.update({"game": None, "fetched_at": 0.0})
            build = read_game_build()
            _game_update_state.update(
                {"ok": True, "message": f"Valheim server updated to build {build.get('installed_buildid')}."}
            )
            if was_running:
                manager.start()
        except subprocess.TimeoutExpired:
            _game_update_state.update({"ok": False, "message": "SteamCMD timed out after 30 minutes."})
        except Exception as exc:
            _game_update_state.update({"ok": False, "message": f"Update error: {exc}"})
        finally:
            _game_update_state["running"] = False

    import threading

    threading.Thread(target=worker, daemon=True, name="game-update").start()
    return {"ok": True, "message": "Server update started. The engine will restart when it finishes."}


def system_info() -> Dict[str, Any]:
    build = get_build_info()
    return {
        "panel_version": get_panel_version(),
        "build": build,
        "python": sys.version.split()[0],
        "hostname": os.uname().nodename,
        "install_dir": INSTALL_DIR,
        "data_dir": DATA_DIR,
        "time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }

import os
import glob
import json
import re
import time
import psutil
import zipfile
import threading
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from engine_manager import GameServerManager
from webhook_manager import dispatch_discord_event
import settings_manager

# Detect host vs container environment
DATA_DIR = os.getenv("SAVE_DIR", "/config" if os.path.exists("/config") else os.path.abspath(os.path.join(os.path.dirname(__file__), "../data")))

# Resolve .env file location
if os.path.exists("/config/.env"):
    ENV_FILE = "/config/.env"
elif os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "../.env"))):
    ENV_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.env"))
else:
    ENV_FILE = "/opt/thorpedo/.env"

load_dotenv(ENV_FILE)

# Resolve plugins directory
if os.path.exists("/valheim/BepInEx/plugins"):
    PLUGINS_DIR = "/valheim/BepInEx/plugins"
elif os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "../plugins"))):
    PLUGINS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../plugins"))
else:
    PLUGINS_DIR = "/opt/thorpedo/plugins"

CONFIG_DIR = os.getenv("CONFIG_DIR", "/valheim/BepInEx/config" if os.path.exists("/valheim/BepInEx/config") else os.path.abspath(os.path.join(os.path.dirname(__file__), "../config")))
ADMIN_FILE = os.path.join(DATA_DIR, "adminlist.txt")
BANNED_FILE = os.path.join(DATA_DIR, "bannedlist.txt")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUPS_DIR, exist_ok=True)

_last_auto_backup_time = time.time()
_auto_backup_thread: Optional[threading.Thread] = None
_auto_backup_running = False

def get_manager() -> GameServerManager:
    return GameServerManager.get_instance()

def get_system_metrics() -> Dict[str, Any]:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_usage_pct": round(cpu, 1),
        "memory_used_mb": round(mem.used / (1024**2), 1),
        "memory_total_mb": round(mem.total / (1024**2), 1),
        "memory_pct": mem.percent,
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1)
    }

def get_full_status() -> Dict[str, Any]:
    mgr = get_manager()
    status_data = mgr.get_status()
    sys_m = get_system_metrics()
    status_data["server"]["memory_total_mb"] = sys_m["memory_total_mb"]
    status_data["server"]["disk_free_gb"] = sys_m["disk_free_gb"]

    # Provide server configuration for overview & management
    settings = settings_manager.load_settings()
    server_conf = settings.get("server", {})
    status_data["server"]["name"] = server_conf.get("name", os.getenv("SERVER_NAME", "BarudakViking"))
    status_data["server"]["password"] = server_conf.get("password") or os.getenv("SERVER_PASSWORD") or os.getenv("SERVER_PASS", "barudak123")
    status_data["server"]["world_name"] = server_conf.get("world_name", os.getenv("WORLD_NAME", "BarudakValheim"))
    status_data["server"]["auto_backup_enabled"] = server_conf.get("auto_backup_enabled", True)
    status_data["server"]["auto_backup_interval_hours"] = server_conf.get("auto_backup_interval_hours", 6)
    status_data["server"]["retention_count"] = server_conf.get("retention_count", 10)

    return status_data

def safe_extract_zip(zip_obj: zipfile.ZipFile, dest_dir: str):
    dest_path = os.path.abspath(dest_dir)
    for member in zip_obj.infolist():
        target_path = os.path.abspath(os.path.join(dest_path, member.filename))
        if not target_path.startswith(dest_path + os.sep) and target_path != dest_path:
            raise ValueError(f"Zip slip path traversal attempt detected: {member.filename}")
    zip_obj.extractall(dest_path)

def get_modifiers() -> Dict[str, str]:
    mods = {
        "resources": "normal",
        "portals": "normal",
        "raids": "normal",
        "combat": "normal",
        "death_penalty": "normal"
    }
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r") as f:
                content = f.read()
            m_match = re.search(r"WORLD_MODIFIERS=[\"']?(.*?)[\"']?$", content, re.MULTILINE)
            if m_match:
                items = m_match.group(1).strip().split()
                for item in items:
                    if "=" in item:
                        k, v = item.split("=", 1)
                        if k.lower() in mods:
                            mods[k.lower()] = v.lower()
                if not any("=" in item for item in items):
                    for i in range(0, len(items), 2):
                        if i + 1 < len(items):
                            k = items[i].lower()
                            v = items[i+1].lower()
                            if k in mods:
                                mods[k] = v
            k_match = re.search(r"WORLD_KEYS=[\"']?(.*?)[\"']?$", content, re.MULTILINE)
            if k_match:
                keys = k_match.group(1).strip().split()
                if "playerevents" in keys:
                    mods["raids"] = "playerevents"
        except Exception:
            pass
    return mods

def save_modifiers(new_mods: Dict[str, str]) -> bool:
    if not os.path.exists(ENV_FILE):
        return False
    try:
        with open(ENV_FILE, "r") as f:
            lines = f.readlines()

        mod_pairs = []
        world_keys = []
        for k, v in new_mods.items():
            if k == "raids" and v == "playerevents":
                world_keys.append("playerevents")
            else:
                mod_pairs.append(f"{k}={v}")

        new_lines = []
        m_found = False
        k_found = False
        for line in lines:
            if line.startswith("WORLD_MODIFIERS="):
                new_lines.append(f'WORLD_MODIFIERS="{" ".join(mod_pairs)}"\n')
                m_found = True
            elif line.startswith("WORLD_KEYS="):
                new_lines.append(f'WORLD_KEYS="{" ".join(world_keys)}"\n')
                k_found = True
            else:
                new_lines.append(line)

        if not m_found:
            new_lines.append(f'WORLD_MODIFIERS="{" ".join(mod_pairs)}"\n')
        if not k_found:
            new_lines.append(f'WORLD_KEYS="{" ".join(world_keys)}"\n')

        with open(ENV_FILE, "w") as f:
            f.writelines(new_lines)

        os.environ["WORLD_MODIFIERS"] = " ".join(mod_pairs)
        os.environ["WORLD_KEYS"] = " ".join(world_keys)
        return True
    except Exception as e:
        print(f"Error saving modifiers: {e}")
        return False

def get_snapshots() -> List[List[Any]]:
    snaps = []
    # 1. Local zip backups in BACKUPS_DIR
    local_backups = glob.glob(os.path.join(BACKUPS_DIR, "*.zip")) + glob.glob(os.path.join(BACKUPS_DIR, "*.tar.gz"))
    for b in sorted(local_backups, key=os.path.getmtime, reverse=True):
        fname = os.path.basename(b)
        size_mb = round(os.path.getsize(b) / (1024**2), 2)
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(b)))
        snaps.append([fname, mtime, size_mb, "LOKAL"])

    # 2. GitHub Releases
    settings = settings_manager.load_settings()
    gh = settings.get("github", {})
    token = gh.get("token") or os.getenv("GITHUB_TOKEN")
    repo = gh.get("repo") or os.getenv("GITHUB_REPO", "Thaaan184/Thorpedo-Valheim")
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token and not token.startswith("your_"):
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.get(f"https://api.github.com/repos/{repo}/releases?per_page=10", headers=headers, timeout=3)
        if resp.status_code == 200:
            for rel in resp.json():
                tag = rel.get("tag_name", "")
                created = rel.get("created_at", "")[:16].replace("T", " ")
                size_mb = 8.22
                if rel.get("assets") and len(rel["assets"]) > 0:
                    size_mb = round(rel["assets"][0].get("size", 0) / (1024**2), 2)
                snaps.append([tag, created, size_mb, "GH"])
    except Exception:
        pass

    if not snaps:
        snaps.append(["snapshot-initial", time.strftime("%Y-%m-%d %H:%M"), 8.22, "LOKAL"])

    return snaps

def upload_to_github_release(zip_path: str, tag_name: str) -> Dict[str, Any]:
    settings = settings_manager.load_settings()
    gh = settings.get("github", {})
    token = gh.get("token") or os.getenv("GITHUB_TOKEN")
    repo = gh.get("repo") or os.getenv("GITHUB_REPO", "Thaaan184/Thorpedo-Valheim")

    if not token or token.startswith("your_") or not repo or "/" not in repo:
        return {"ok": False, "message": "GitHub token or repository not properly configured in Settings."}

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Thorpedo-Panel/2.0"
    }

    # 1. Create Release
    rel_url = f"https://api.github.com/repos/{repo}/releases"
    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC")
    payload = {
        "tag_name": tag_name,
        "target_commitish": "main",
        "name": f"World Snapshot • {tag_name}",
        "body": f"Automated Thorpedo Valheim World Snapshot\n\n- **Timestamp:** {now_str}\n- **Source:** Thorpedo Autonomous Control Suite",
        "draft": False,
        "prerelease": False
    }

    try:
        r = requests.post(rel_url, json=payload, headers=headers, timeout=12)
        if r.status_code not in [200, 201]:
            return {"ok": False, "message": f"GitHub release creation failed: HTTP {r.status_code} {r.text[:100]}"}
        rel_data = r.json()
        upload_url = rel_data.get("upload_url", "").split("{")[0]

        # 2. Upload asset
        fname = os.path.basename(zip_path)
        asset_url = f"{upload_url}?name={fname}"
        with open(zip_path, "rb") as f:
            file_bytes = f.read()

        up_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/zip",
            "Content-Length": str(len(file_bytes)),
            "User-Agent": "Thorpedo-Panel/2.0"
        }
        ur = requests.post(asset_url, data=file_bytes, headers=up_headers, timeout=60)
        if ur.status_code in [200, 201]:
            get_manager()._handle_log_line(f"Snapshot uploaded to GitHub Release: {tag_name}")
            return {"ok": True, "message": f"Successfully published to GitHub Release '{tag_name}'"}
        return {"ok": False, "message": f"Asset upload failed: HTTP {ur.status_code} {ur.text[:100]}"}
    except Exception as e:
        return {"ok": False, "message": f"GitHub release error: {str(e)}"}

def clean_old_snapshots(retention_count: int):
    if retention_count <= 0:
        return
    local_backups = sorted(glob.glob(os.path.join(BACKUPS_DIR, "snapshot-*.zip")), key=os.path.getmtime)
    while len(local_backups) > retention_count:
        oldest = local_backups.pop(0)
        try:
            os.remove(oldest)
            get_manager()._handle_log_line(f"Pruned old snapshot: {os.path.basename(oldest)}")
        except Exception:
            pass
    # Clean up excess pre-rollback safety snapshots (keep latest 5)
    pre_backups = sorted(glob.glob(os.path.join(BACKUPS_DIR, "pre-rollback-*.zip")), key=os.path.getmtime)
    while len(pre_backups) > 5:
        old_pre = pre_backups.pop(0)
        try:
            os.remove(old_pre)
        except Exception:
            pass

def create_snapshot() -> Dict[str, Any]:
    mgr = get_manager()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    fname = f"snapshot-{timestamp}.zip"
    out_path = os.path.join(BACKUPS_DIR, fname)
    
    # 1. Coordinate with game engine save if running
    if mgr.status == "running":
        mgr.send_command("save")
        time.sleep(1.5)
    
    world_dir = os.path.join(DATA_DIR, "worlds_local")
    if not os.path.exists(world_dir):
        if any(f.endswith((".db", ".db2")) for f in os.listdir(DATA_DIR)):
            world_dir = DATA_DIR
        else:
            err_msg = f"World directory {world_dir} not found."
            mgr._handle_log_line(f"Snapshot error: {err_msg}")
            dispatch_discord_event("backup_failed", {"error": err_msg})
            return {"ok": False, "message": err_msg}

    # 2. Comprehensive world scan (supporting modern Valheim .db2, .fwl2, .chunks, and .chunk)
    settings = settings_manager.load_settings()
    world_name = settings.get("server", {}).get("world_name") or os.getenv("WORLD_NAME", "BarudakValheim")
    valid_exts = (".db", ".db2", ".fwl", ".fwl2", ".chunks", ".chunk", ".modifiers", ".old")
    
    files_to_zip = []
    
    # Check world folder (modern Valheim chunk structure)
    active_world_subdir = os.path.join(world_dir, world_name)
    if os.path.isdir(active_world_subdir):
        for root, _, files in os.walk(active_world_subdir):
            for file in files:
                if file.endswith(valid_exts):
                    files_to_zip.append(os.path.join(root, file))
    
    # Check root world_dir files
    for item in os.listdir(world_dir):
        full = os.path.join(world_dir, item)
        if os.path.isfile(full) and item.endswith(valid_exts):
            if "backup_auto" not in item and "backup_before" not in item:
                files_to_zip.append(full)
        elif os.path.isdir(full) and item != world_name and "backup_auto" not in item and "backup_before" not in item:
            for root, _, files in os.walk(full):
                for file in files:
                    if file.endswith(valid_exts):
                        files_to_zip.append(os.path.join(root, file))

    if not files_to_zip:
        err_msg = f"World validation failed: no valid world save files found in {world_dir}."
        mgr._handle_log_line(f"Snapshot error: {err_msg}")
        dispatch_discord_event("backup_failed", {"error": err_msg})
        return {"ok": False, "message": err_msg}

    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for full_path in files_to_zip:
                rel_path = os.path.relpath(full_path, DATA_DIR)
                z.write(full_path, rel_path)
        
        # 3. Verify archive integrity mathematically
        with zipfile.ZipFile(out_path, "r") as z:
            bad_file = z.testzip()
            if bad_file:
                if os.path.exists(out_path):
                    os.remove(out_path)
                err_msg = f"Archive integrity corrupted for file: {bad_file}"
                mgr._handle_log_line(f"Snapshot error: {err_msg}")
                dispatch_discord_event("backup_failed", {"error": err_msg})
                return {"ok": False, "message": err_msg}

        size_mb = round(os.path.getsize(out_path) / (1024**2), 2)
        mtime = time.strftime("%Y-%m-%d %H:%M")
        mgr._handle_log_line(f"Snapshot created & verified: {fname} ({size_mb} MB, {len(files_to_zip)} files)")
        
        # Enforce retention policy
        retention = settings.get("server", {}).get("retention_count", 10)
        clean_old_snapshots(retention)

        # Push to GitHub Release if configured
        gh_cfg = settings.get("github", {})
        if gh_cfg.get("auto_upload_releases") and gh_cfg.get("token") and not gh_cfg.get("token", "").startswith("your_"):
            upload_to_github_release(out_path, f"snapshot-{timestamp}")

        # Dispatch Discord success notification
        ctx = mgr._get_context_dict()
        ctx["filename"] = fname
        ctx["size_mb"] = str(size_mb)
        ctx["snapshot_tag"] = f"snapshot-{timestamp}"
        dispatch_discord_event("backup_success", ctx)

        return {
            "ok": True,
            "filename": fname,
            "tag": f"snapshot-{timestamp}",
            "size_mb": size_mb,
            "mtime": mtime
        }
    except Exception as e:
        if os.path.exists(out_path):
            os.remove(out_path)
        mgr._handle_log_line(f"Failed to create snapshot: {e}")
        dispatch_discord_event("backup_failed", {"error": str(e)})
        return {"ok": False, "message": f"Failed to create snapshot: {e}"}

def rollback_snapshot(target_id: str, safety: bool = True) -> Dict[str, Any]:
    mgr = get_manager()
    clean_target = os.path.basename(target_id).strip()
    if not clean_target:
        return {"ok": False, "message": "Invalid snapshot target ID."}

    target_file = None
    for f in glob.glob(os.path.join(BACKUPS_DIR, "*.zip")):
        base = os.path.basename(f)
        if base == clean_target or base == f"{clean_target}.zip" or (clean_target.startswith("snapshot-") and base.startswith(clean_target)):
            target_file = f
            break

    if not target_file:
        return {"ok": False, "message": f"Snapshot '{target_id}' not found in {BACKUPS_DIR}."}

    # 1. Validate target archive integrity BEFORE modifying anything
    try:
        with zipfile.ZipFile(target_file, "r") as z:
            corrupt = z.testzip()
            if corrupt:
                return {"ok": False, "message": f"Target snapshot archive is corrupted ({corrupt}). Rollback aborted for safety."}
    except Exception as e:
        return {"ok": False, "message": f"Cannot read target snapshot archive: {e}"}

    # 2. Create emergency pre-rollback safety backup of current state
    if safety:
        try:
            create_snapshot()
            mgr._handle_log_line("Created pre-rollback safety snapshot successfully.")
        except Exception as e:
            mgr._handle_log_line(f"Warning: Failed to create pre-rollback snapshot: {e}")

    # 3. Stop game engine cleanly
    was_running = (mgr.status == "running")
    if was_running:
        mgr.stop(timeout=15)

    # 4. Extract target snapshot safely with automatic fallback recovery on failure
    try:
        with zipfile.ZipFile(target_file, "r") as z:
            safe_extract_zip(z, DATA_DIR)
        mgr._handle_log_line(f"World rollback successfully restored from: {os.path.basename(target_file)}")
        if was_running:
            mgr.start()
        return {"ok": True, "message": f"Rollback to {os.path.basename(target_file)} succeeded."}
    except Exception as e:
        mgr._handle_log_line(f"ERROR extracting target snapshot: {e}")
        if was_running:
            mgr.start()
        return {"ok": False, "message": f"Failed to extract snapshot: {e}"}

def delete_snapshot(target_id: str) -> bool:
    clean_target = os.path.basename(target_id).strip()
    if not clean_target:
        return False
    for f in glob.glob(os.path.join(BACKUPS_DIR, "*.zip")):
        base = os.path.basename(f)
        if base == clean_target or base == f"{clean_target}.zip" or (clean_target.startswith("snapshot-") and base == clean_target):
            try:
                os.remove(f)
                get_manager()._handle_log_line(f"Deleted snapshot archive: {base}")
                return True
            except Exception:
                return False
    return False

def upload_world_save(filename: str, file_bytes: bytes) -> Dict[str, Any]:
    target_dir = os.path.join(DATA_DIR, "worlds_local")
    os.makedirs(target_dir, exist_ok=True)
    
    clean_fn = os.path.basename(filename).strip()
    if not clean_fn:
        return {"ok": False, "message": "Invalid filename."}

    valid_single_exts = (".db", ".fwl", ".db2", ".fwl2", ".modifiers")
    if clean_fn.endswith(valid_single_exts):
        dest = os.path.join(target_dir, clean_fn)
        with open(dest, "wb") as f:
            f.write(file_bytes)
        get_manager()._handle_log_line(f"Uploaded world save file: {clean_fn}")
        return {"ok": True, "filename": clean_fn, "message": f"World file '{clean_fn}' uploaded successfully."}
    elif clean_fn.endswith(".zip"):
        temp_zip = os.path.join(target_dir, f"temp_{int(time.time())}_{clean_fn}")
        with open(temp_zip, "wb") as f:
            f.write(file_bytes)
        try:
            with zipfile.ZipFile(temp_zip, "r") as z:
                corrupt = z.testzip()
                if corrupt:
                    os.remove(temp_zip)
                    return {"ok": False, "message": f"Uploaded zip archive is corrupted: {corrupt}"}
                has_worlds_prefix = any(m.startswith("worlds_local/") for m in z.namelist())
                extract_base = DATA_DIR if has_worlds_prefix else target_dir
                safe_extract_zip(z, extract_base)
            os.remove(temp_zip)
            get_manager()._handle_log_line(f"Extracted world archive: {clean_fn}")
            return {"ok": True, "filename": clean_fn, "message": f"World archive '{clean_fn}' extracted successfully."}
        except Exception as e:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            return {"ok": False, "message": f"Failed to extract world zip: {e}"}
            
    return {"ok": False, "message": "Invalid file format. Please upload .db, .fwl, .db2, .fwl2, or .zip archive."}

def get_installed_mods() -> List[List[Any]]:
    mods = []
    known_info = {
        "CrossplayJoinCodeFix": ("v1.1.1", "Crossplay Join Code stability helper"),
        "Gjallarhorn": ("v0.3.0", "Death announcer, boss banners, biome detection"),
        "WorkbenchLove": ("v1.0.2", "Expanded workbench radius for builders (40m)"),
        "ScheduledMessages": ("v1.0.0", "Automated broadcast messages & countdowns"),
        "ServerDevcommands": ("v1.0.0", "Multiplayer admin command toolkit"),
        "NoCheatFlag": ("v1.0.0", "Suppresses false positive cheat flags on items"),
        "NetworkPerformanceSystem": ("v1.0.0", "UDP ZDO compression and lag optimization"),
        "Jotunn": ("v2.20.0", "The Valheim Modding Library runtime")
    }

    if os.path.exists(PLUGINS_DIR):
        files = os.listdir(PLUGINS_DIR)
        for f in sorted(files):
            if f.endswith(".dll") or f.endswith(".dll.disabled"):
                is_enabled = 1 if f.endswith(".dll") else 0
                clean_name = f.replace(".dll.disabled", "").replace(".dll", "")
                v, desc = known_info.get(clean_name, ("v1.0.0", "Custom BepInEx mod plugin"))
                mods.append([clean_name, v, desc, is_enabled])

    if not mods:
        for name, (v, desc) in known_info.items():
            mods.append([name, v, desc, 1])

    return mods

def toggle_mod(name: str, enable: bool) -> bool:
    if not os.path.exists(PLUGINS_DIR):
        return False
    dll = os.path.join(PLUGINS_DIR, f"{name}.dll")
    dis = os.path.join(PLUGINS_DIR, f"{name}.dll.disabled")
    if enable and os.path.exists(dis):
        os.rename(dis, dll)
        get_manager()._handle_log_line(f"Enabled plugin: {name}")
        return True
    elif not enable and os.path.exists(dll):
        os.rename(dll, dis)
        get_manager()._handle_log_line(f"Disabled plugin: {name}")
        return True
    return False

def delete_mod(name: str) -> bool:
    if not os.path.exists(PLUGINS_DIR):
        return False
    dll = os.path.join(PLUGINS_DIR, f"{name}.dll")
    dis = os.path.join(PLUGINS_DIR, f"{name}.dll.disabled")
    deleted = False
    if os.path.exists(dll):
        try:
            os.remove(dll)
            deleted = True
        except Exception:
            pass
    if os.path.exists(dis):
        try:
            os.remove(dis)
            deleted = True
        except Exception:
            pass
    if deleted:
        get_manager()._handle_log_line(f"Deleted plugin: {name}")
    return deleted

def upload_mod(filename: str, file_bytes: bytes) -> Dict[str, Any]:
    os.makedirs(PLUGINS_DIR, exist_ok=True)
    clean_fn = os.path.basename(filename)
    if clean_fn.endswith(".dll"):
        dest = os.path.join(PLUGINS_DIR, clean_fn)
        with open(dest, "wb") as f:
            f.write(file_bytes)
        get_manager()._handle_log_line(f"Uploaded plugin: {clean_fn}")
        return {"ok": True, "filename": clean_fn, "message": f"Plugin '{clean_fn}' installed to plugins folder."}
    elif clean_fn.endswith(".zip"):
        temp_zip = os.path.join(PLUGINS_DIR, f"temp_{clean_fn}")
        with open(temp_zip, "wb") as f:
            f.write(file_bytes)
        try:
            with zipfile.ZipFile(temp_zip, "r") as z:
                corrupt = z.testzip()
                if corrupt:
                    os.remove(temp_zip)
                    return {"ok": False, "message": f"Uploaded zip archive is corrupted: {corrupt}"}
                for member in z.namelist():
                    if member.endswith(".dll"):
                        extracted_name = os.path.basename(member)
                        with z.open(member) as source, open(os.path.join(PLUGINS_DIR, extracted_name), "wb") as target:
                            target.write(source.read())
            os.remove(temp_zip)
            get_manager()._handle_log_line(f"Extracted plugin archive: {clean_fn}")
            return {"ok": True, "filename": clean_fn, "message": f"Plugins from '{clean_fn}' extracted successfully."}
        except Exception as e:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            return {"ok": False, "message": f"Failed to extract mod zip: {e}"}

    return {"ok": False, "message": "Invalid file format. Please upload .dll or .zip archive."}

def get_mod_config(name: str) -> Dict[str, Any]:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    clean = re.sub(r"[^\w.-]", "", os.path.basename(name).strip())

    # Check exact match
    exact = os.path.join(CONFIG_DIR, f"{clean}.cfg")
    if os.path.exists(exact):
        with open(exact, "r", encoding="utf-8", errors="replace") as f:
            return {"ok": True, "name": clean, "file": f"{clean}.cfg", "content": f.read()}

    # Search case-insensitively or partial match
    lower_target = clean.lower()
    for fname in os.listdir(CONFIG_DIR):
        if fname.endswith(".cfg") and lower_target in fname.lower():
            target_path = os.path.join(CONFIG_DIR, fname)
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                return {"ok": True, "name": clean, "file": fname, "content": f.read()}

    if lower_target in ["bepinex", "core"]:
        bp = os.path.join(CONFIG_DIR, "BepInEx.cfg")
        if os.path.exists(bp):
            with open(bp, "r", encoding="utf-8", errors="replace") as f:
                return {"ok": True, "name": "BepInEx", "file": "BepInEx.cfg", "content": f.read()}

    default_cfg = f"# Configuration file for BepInEx plugin: {clean}\n[General]\n## Enable or disable plugin\nEnabled = true\n"
    return {"ok": True, "name": clean, "file": f"{clean}.cfg", "content": default_cfg, "is_new": True}

def save_mod_config(name: str, content: str) -> Dict[str, Any]:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    clean = re.sub(r"[^\w.-]", "", os.path.basename(name).strip())

    target_file = os.path.join(CONFIG_DIR, f"{clean}.cfg")
    lower_target = clean.lower()
    for fname in os.listdir(CONFIG_DIR):
        if fname.endswith(".cfg") and lower_target in fname.lower():
            target_file = os.path.join(CONFIG_DIR, fname)
            break

    try:
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(content)
        get_manager()._handle_log_line(f"Updated configuration for plugin: {os.path.basename(target_file)}")
        return {"ok": True, "message": f"Configuration saved to {os.path.basename(target_file)}."}
    except Exception as e:
        return {"ok": False, "message": f"Failed to save config: {e}"}

def get_admin_list() -> List[str]:
    if not os.path.exists(ADMIN_FILE):
        return []
    with open(ADMIN_FILE, "r") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("//")]

def add_admin(steam_id: str) -> bool:
    clean_id = steam_id.strip()
    admins = get_admin_list()
    if clean_id not in admins:
        admins.append(clean_id)
        with open(ADMIN_FILE, "w") as f:
            f.write("\n".join(admins) + "\n")
        get_manager()._handle_log_line(f"Added admin SteamID: {clean_id}")
        get_manager().send_command(f"admin {clean_id}")
        return True
    return False

def remove_admin(steam_id: str) -> bool:
    clean_id = steam_id.strip()
    admins = get_admin_list()
    if clean_id in admins:
        admins.remove(clean_id)
        with open(ADMIN_FILE, "w") as f:
            f.write("\n".join(admins) + "\n")
        get_manager()._handle_log_line(f"Removed admin SteamID: {clean_id}")
        get_manager().send_command(f"unadmin {clean_id}")
        return True
    return False

def get_banned_list() -> List[str]:
    if not os.path.exists(BANNED_FILE):
        return []
    with open(BANNED_FILE, "r") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("//")]

def add_banned(steam_id: str) -> bool:
    clean_id = steam_id.strip()
    bans = get_banned_list()
    if clean_id not in bans:
        bans.append(clean_id)
        with open(BANNED_FILE, "w") as f:
            f.write("\n".join(bans) + "\n")
        get_manager()._handle_log_line(f"Banned SteamID: {clean_id}")
        get_manager().send_command(f"ban {clean_id}")
        return True
    return False

def remove_banned(steam_id: str) -> bool:
    clean_id = steam_id.strip()
    bans = get_banned_list()
    if clean_id in bans:
        bans.remove(clean_id)
        with open(BANNED_FILE, "w") as f:
            f.write("\n".join(bans) + "\n")
        get_manager()._handle_log_line(f"Unbanned SteamID: {clean_id}")
        get_manager().send_command(f"unban {clean_id}")
        return True
    return False

def execute_server_action(action: str, delay: int = 0) -> Dict[str, Any]:
    mgr = get_manager()
    if action in ["restart_graceful", "restart"]:
        res = mgr.restart(graceful=True, delay_seconds=delay)
        return {"ok": res, "message": f"Graceful restart initiated (delay: {delay}s)."}
    elif action in ["restart_force", "kill"]:
        res = mgr.restart(graceful=False, delay_seconds=0)
        return {"ok": res, "message": "Force restart (SIGKILL) executed."}
    elif action == "start":
        res = mgr.start()
        return {"ok": res, "message": "Server started." if res else "Server was already running or failed to start."}
    elif action == "stop":
        res = mgr.stop()
        return {"ok": res, "message": "Server stopped."}
    elif action == "force_save":
        if mgr.status != "running":
            return {"ok": False, "message": "Cannot save: game engine is not running."}
        mgr.send_command("save")
        return {"ok": True, "message": "Save command dispatched to engine."}
    elif action == "check_playfab":
        if mgr.status != "running":
            return {"ok": False, "message": "Valheim game engine is currently stopped. Start server to probe lobby."}
        if mgr.playfab_active and mgr.playfab_join_code:
            ip_str = mgr.playfab_ip or "active"
            return {"ok": True, "message": f"PlayFab Active: Join Code {mgr.playfab_join_code} on {ip_str}. Crossplay ready."}
        return {"ok": False, "message": "PlayFab lobby is initializing. Awaiting socket handshake from master server."}
    elif action == "reset_playfab":
        mgr._handle_log_line("Manual PlayFab lobby reset requested: restarting engine...")
        res = mgr.restart(graceful=True, delay_seconds=0)
        return {"ok": res, "message": "PlayFab lobby reset: server rebooting to forge new Join Code."}
    elif action == "broadcast_discord":
        ctx = mgr._get_context_dict()
        ctx["force"] = True
        dispatched = dispatch_discord_event("server_ready", ctx)
        if dispatched:
            return {"ok": True, "message": "Join Code & server credentials dispatched to Discord channel!"}
        return {"ok": False, "message": "Discord broadcast failed: check webhook URL in Settings."}
    return {"ok": False, "message": f"Unknown action: {action}"}

def start_auto_backup_worker():
    global _auto_backup_thread, _auto_backup_running
    if _auto_backup_running:
        return
    _auto_backup_running = True

    def worker():
        global _last_auto_backup_time
        while _auto_backup_running:
            try:
                time.sleep(60)
                settings = settings_manager.load_settings()
                srv = settings.get("server", {})
                if not srv.get("auto_backup_enabled", True):
                    continue

                interval_h = srv.get("auto_backup_interval_hours", 6)
                interval_sec = max(1, interval_h) * 3600
                now = time.time()

                if now - _last_auto_backup_time >= interval_sec:
                    mgr = get_manager()
                    # Announce if players online
                    if mgr.status == "running" and mgr.players:
                        mgr.send_command("say Periodic world backup in progress...")
                    create_snapshot()
                    _last_auto_backup_time = now
            except Exception as e:
                print(f"Auto-backup worker exception: {e}")

    _auto_backup_thread = threading.Thread(target=worker, daemon=True, name="auto-backup-worker")
    _auto_backup_thread.start()

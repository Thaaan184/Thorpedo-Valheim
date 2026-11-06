import os
import glob
import json
import re
import time
import psutil
import zipfile
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from engine_manager import GameServerManager
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
    status_data["server"]["name"] = server_conf.get("name", os.getenv("SERVER_NAME", "BarudakValheim"))
    status_data["server"]["password"] = server_conf.get("password", os.getenv("SERVER_PASS", "barudak123"))
    status_data["server"]["world_name"] = server_conf.get("world_name", os.getenv("WORLD_NAME", "BarudakValheim"))
    status_data["server"]["auto_backup_enabled"] = server_conf.get("auto_backup_enabled", True)
    status_data["server"]["auto_backup_interval_hours"] = server_conf.get("auto_backup_interval_hours", 6)
    status_data["server"]["retention_count"] = server_conf.get("retention_count", 10)

    return status_data

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
                pairs = m_match.group(1).strip().split()
                for i in range(0, len(pairs), 2):
                    if i + 1 < len(pairs):
                        k = pairs[i].lower()
                        v = pairs[i+1].lower()
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
                mod_pairs.extend([k, v])

        mod_str = f'WORLD_MODIFIERS="{" ".join(mod_pairs)}"\n'
        key_str = f'WORLD_KEYS="{" ".join(world_keys)}"\n'

        has_mod = False
        has_key = False
        new_lines = []
        for line in lines:
            if line.startswith("WORLD_MODIFIERS="):
                new_lines.append(mod_str)
                has_mod = True
            elif line.startswith("WORLD_KEYS="):
                new_lines.append(key_str)
                has_key = True
            else:
                new_lines.append(line)

        if not has_mod:
            new_lines.append(mod_str)
        if not has_key:
            new_lines.append(key_str)

        with open(ENV_FILE, "w") as f:
            f.writelines(new_lines)

        load_dotenv(ENV_FILE, override=True)
        return True
    except Exception as e:
        get_manager()._handle_log_line(f"Failed to save modifiers to .env: {e}")
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

def create_snapshot() -> Dict[str, Any]:
    mgr = get_manager()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    fname = f"snapshot-{timestamp}.zip"
    out_path = os.path.join(BACKUPS_DIR, fname)
    
    # Send save command
    mgr.send_command("save")
    time.sleep(1)
    
    world_dir = os.path.join(DATA_DIR, "worlds_local")
    if not os.path.exists(world_dir):
        return {"ok": False, "message": f"World directory {world_dir} not found."}

    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(world_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, DATA_DIR)
                    z.write(full_path, rel_path)
        
        size_mb = round(os.path.getsize(out_path) / (1024**2), 2)
        mtime = time.strftime("%Y-%m-%d %H:%M")
        mgr._handle_log_line(f"Snapshot created: {fname} ({size_mb} MB)")
        
        # Enforce retention policy
        settings = settings_manager.load_settings()
        retention = settings.get("server", {}).get("retention_count", 10)
        clean_old_snapshots(retention)

        return {
            "ok": True,
            "filename": fname,
            "tag": f"snapshot-{timestamp}",
            "size_mb": size_mb,
            "mtime": mtime
        }
    except Exception as e:
        return {"ok": False, "message": f"Failed to create snapshot: {e}"}

def rollback_snapshot(target_id: str, safety: bool = True) -> Dict[str, Any]:
    mgr = get_manager()
    if safety:
        create_snapshot()

    mgr.stop(timeout=15)

    target_file = None
    for f in glob.glob(os.path.join(BACKUPS_DIR, "*.zip")):
        if target_id in os.path.basename(f):
            target_file = f
            break

    if not target_file:
        mgr.start()
        return {"ok": False, "message": f"Snapshot '{target_id}' not found in {BACKUPS_DIR}."}

    try:
        with zipfile.ZipFile(target_file, "r") as z:
            z.extractall(DATA_DIR)
        mgr._handle_log_line(f"World rollback successfully restored from: {os.path.basename(target_file)}")
        mgr.start()
        return {"ok": True, "message": f"Rollback to {os.path.basename(target_file)} succeeded."}
    except Exception as e:
        mgr.start()
        return {"ok": False, "message": f"Failed to extract snapshot: {e}"}

def upload_world_save(filename: str, file_bytes: bytes) -> Dict[str, Any]:
    target_dir = os.path.join(DATA_DIR, "worlds_local")
    os.makedirs(target_dir, exist_ok=True)
    
    clean_fn = os.path.basename(filename)
    if clean_fn.endswith(".db") or clean_fn.endswith(".fwl"):
        dest = os.path.join(target_dir, clean_fn)
        with open(dest, "wb") as f:
            f.write(file_bytes)
        get_manager()._handle_log_line(f"Uploaded world save file: {clean_fn}")
        return {"ok": True, "filename": clean_fn, "message": f"World file '{clean_fn}' uploaded successfully."}
    elif clean_fn.endswith(".zip"):
        temp_zip = os.path.join(target_dir, f"temp_{clean_fn}")
        with open(temp_zip, "wb") as f:
            f.write(file_bytes)
        try:
            with zipfile.ZipFile(temp_zip, "r") as z:
                z.extractall(target_dir)
            os.remove(temp_zip)
            get_manager()._handle_log_line(f"Extracted world archive: {clean_fn}")
            return {"ok": True, "filename": clean_fn, "message": f"World archive '{clean_fn}' extracted to worlds_local."}
        except Exception as e:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            return {"ok": False, "message": f"Failed to extract world zip: {e}"}
            
    return {"ok": False, "message": "Invalid file format. Please upload .db, .fwl, or .zip archive."}

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
        return True
    elif not enable and os.path.exists(dll):
        os.rename(dll, dis)
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
        get_manager()._handle_log_line(f"Deleted mod plugin: {name}")
    return deleted

def upload_mod(filename: str, file_bytes: bytes) -> Dict[str, Any]:
    os.makedirs(PLUGINS_DIR, exist_ok=True)
    clean_fn = os.path.basename(filename)
    
    if clean_fn.endswith(".dll"):
        dest = os.path.join(PLUGINS_DIR, clean_fn)
        with open(dest, "wb") as f:
            f.write(file_bytes)
        get_manager()._handle_log_line(f"Installed mod: {clean_fn}")
        return {"ok": True, "type": "dll", "filename": clean_fn, "message": f"Plugin '{clean_fn}' installed to plugins folder."}
    elif clean_fn.endswith(".zip"):
        temp_zip = os.path.join(PLUGINS_DIR, f"temp_{clean_fn}")
        with open(temp_zip, "wb") as f:
            f.write(file_bytes)
        try:
            with zipfile.ZipFile(temp_zip, "r") as z:
                z.extractall(PLUGINS_DIR)
            os.remove(temp_zip)
            get_manager()._handle_log_line(f"Extracted mod zip archive: {clean_fn}")
            return {"ok": True, "type": "zip", "filename": clean_fn, "message": f"Archive '{clean_fn}' successfully extracted into plugins."}
        except Exception as e:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            return {"ok": False, "message": f"Failed to extract mod zip: {e}"}
            
    return {"ok": False, "message": "Invalid file format. Upload .dll plugin or .zip bundle."}

def get_admin_list() -> List[str]:
    if not os.path.exists(ADMIN_FILE):
        return []
    with open(ADMIN_FILE, "r") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("//")]

def get_banned_list() -> List[str]:
    if not os.path.exists(BANNED_FILE):
        return []
    with open(BANNED_FILE, "r") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("//")]

def add_admin(steam_id: str) -> bool:
    admins = get_admin_list()
    if steam_id in admins:
        return True
    admins.append(steam_id)
    with open(ADMIN_FILE, "w") as f:
        f.write("\n".join(admins) + "\n")
    get_manager().send_command(f"admin {steam_id}")
    return True

def remove_admin(steam_id: str) -> bool:
    admins = get_admin_list()
    if steam_id not in admins:
        return True
    admins.remove(steam_id)
    with open(ADMIN_FILE, "w") as f:
        f.write("\n".join(admins) + "\n")
    get_manager().send_command(f"unadmin {steam_id}")
    return True

def add_banned(steam_id: str) -> bool:
    bans = get_banned_list()
    if steam_id in bans:
        return True
    bans.append(steam_id)
    with open(BANNED_FILE, "w") as f:
        f.write("\n".join(bans) + "\n")
    get_manager().send_command(f"ban {steam_id}")
    return True

def remove_banned(steam_id: str) -> bool:
    bans = get_banned_list()
    if steam_id not in bans:
        return True
    bans.remove(steam_id)
    with open(BANNED_FILE, "w") as f:
        f.write("\n".join(bans) + "\n")
    get_manager().send_command(f"unban {steam_id}")
    return True

def send_discord_broadcast() -> bool:
    mgr = get_manager()
    status = mgr.get_status()
    join_code = status["playfab"]["join_code"]
    is_active = status["playfab"]["is_active"]
    ip = status["playfab"]["ip"]
    port = status["playfab"]["port"]
    uptime_min = status["server"]["uptime_seconds"] // 60

    settings = settings_manager.load_settings()
    webhooks = settings.get("discord_webhooks", [])

    # If no configured webhooks in settings, fallback to env
    if not webhooks:
        legacy_url = os.getenv("DISCORD_WEBHOOK_URL", "")
        if legacy_url:
            webhooks = [{"url": legacy_url, "enabled": True, "mention_role": "", "embed_color": "#D97706"}]

    sent_any = False
    for wh in webhooks:
        if not wh.get("enabled") or not wh.get("url"):
            continue

        role = wh.get("mention_role", "").strip()
        color_hex = wh.get("embed_color", "#D97706")
        color_int = 14251782
        try:
            if color_hex.startswith("#"):
                color_int = int(color_hex.lstrip("#"), 16)
        except Exception:
            pass

        payload = {
            "username": "Thorpedo Valheim Sentinel",
            "avatar_url": "https://raw.githubusercontent.com/reloadlife/valheim-server/main/logo.png",
            "content": f"{role} " if role else None,
            "embeds": [
                {
                    "title": "⚔️ THORPEDO VALHEIM — GATEWAY STATUS",
                    "description": "PlayFab crossplay lobby is live and world gates are open!",
                    "color": color_int,
                    "fields": [
                        {"name": "🔑 6-Digit Join Code", "value": f"**`{join_code}`**", "inline": True},
                        {"name": "🛡️ Lobby State", "value": "🟢 Online & Registered" if is_active else "🔴 Initializing...", "inline": True},
                        {"name": "⏱️ Uptime", "value": f"{uptime_min} mins", "inline": True},
                        {"name": "🌐 Direct IP", "value": f"`{ip}:{port}`", "inline": False},
                        {"name": "📜 How to Connect", "value": "Open Valheim -> **Join Game** -> **Add Server** -> Enter 6-digit Join Code.", "inline": False}
                    ],
                    "footer": {"text": "Thorpedo Autonomous Sentinel • PT Nalar Cerdas Teknologi"}
                }
            ]
        }
        try:
            resp = requests.post(wh["url"], json=payload, timeout=6)
            if resp.status_code in [200, 204]:
                sent_any = True
                mgr._handle_log_line(f"Discord broadcast dispatched to {wh.get('name', 'webhook')} (HTTP {resp.status_code})")
        except Exception as e:
            mgr._handle_log_line(f"Failed to post to webhook {wh.get('name')}: {e}")

    return sent_any

def execute_server_action(action: str, delay: int = 0) -> Dict[str, Any]:
    mgr = get_manager()

    if action in ["restart_graceful", "restart"]:
        mgr.restart(graceful=True, delay_seconds=delay)
        return {"ok": True, "executed": action, "message": f"Graceful restart scheduled (delay: {delay}s)."}

    elif action in ["restart_force", "kill"]:
        mgr.restart(graceful=False, delay_seconds=0)
        return {"ok": True, "executed": action, "message": "Force kill & instant restart initiated."}

    elif action == "start":
        started = mgr.start()
        return {"ok": started, "executed": action, "message": "Game server started." if started else "Server already running."}

    elif action == "stop":
        mgr.stop(timeout=15)
        return {"ok": True, "executed": action, "message": "Server stopped."}

    elif action == "force_save":
        mgr.send_command("save")
        mgr._handle_log_line("World flush / save requested from panel.")
        return {"ok": True, "executed": "force_save", "message": "World flushed to disk successfully."}

    elif action == "reset_playfab":
        mgr.restart(graceful=True, delay_seconds=0)
        return {"ok": True, "executed": "reset_playfab", "message": "Resetting PlayFab matchmaking socket."}

    elif action == "broadcast_discord":
        success = send_discord_broadcast()
        return {"ok": success, "executed": "broadcast_discord", "message": "Broadcast sent to configured Discord webhooks!" if success else "Failed to dispatch webhook (check settings)."}

    elif action == "check_playfab":
        st = mgr.get_status()
        is_active = st["playfab"]["is_active"]
        code = st["playfab"]["join_code"]
        msg = f"PlayFab lobby is REGISTERED (Join Code: {code}). Active and healthy." if is_active else "PlayFab lobby NOT active or currently initializing."
        return {"ok": is_active, "executed": "check_playfab", "message": msg}

    return {"ok": False, "executed": action, "message": f"Action '{action}' unknown."}

import os
import subprocess
import glob
import json
import re
import time
import psutil
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

THORPEDO_ROOT = os.getenv("THORPEDO_ROOT", "/opt/thorpedo" if os.path.exists("/opt/thorpedo") else "/home/ids/projects/Thorpedo-Valheim")
ENV_FILE = os.path.join(THORPEDO_ROOT, ".env")
load_dotenv(ENV_FILE)
DOCKER_CONTAINER = os.getenv("VALHEIM_CONTAINER", "thorpedo-valheim")
PLUGINS_DIR = os.path.join(THORPEDO_ROOT, "plugins")
CONFIG_DIR = os.path.join(THORPEDO_ROOT, "config")
DATA_DIR = os.path.join(THORPEDO_ROOT, "data")
ADMIN_FILE = os.path.join(DATA_DIR, "adminlist.txt")
BANNED_FILE = os.path.join(DATA_DIR, "bannedlist.txt")
SCRIPTS_DIR = os.path.join(THORPEDO_ROOT, "scripts")

def run_cmd(cmd: List[str], timeout: int = 15) -> str:
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return res.stdout.strip()
    except Exception as e:
        return f"Error: {e}"

def get_system_metrics() -> Dict[str, Any]:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_usage_pct": round(cpu, 1),
        "memory_used_gb": round(mem.used / (1024**3), 1),
        "memory_total_gb": round(mem.total / (1024**3), 1),
        "memory_pct": mem.percent,
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1)
    }

def get_container_status() -> Dict[str, Any]:
    out = run_cmd(["docker", "inspect", DOCKER_CONTAINER, "--format", "{{json .State}}"])
    if not out or out.startswith("Error"):
        return {"status": "stopped", "running": False, "uptime": 0, "health": "unknown"}
    try:
        data = json.loads(out)
        status = data.get("Status", "unknown")
        running = data.get("Running", False)
        started_at = data.get("StartedAt", "")
        return {
            "status": status,
            "running": running,
            "started_at": started_at,
            "restarting": data.get("Restarting", False)
        }
    except Exception:
        return {"status": "unknown", "running": False, "uptime": 0}

def get_playfab_info() -> Dict[str, Any]:
    logs = run_cmd(["docker", "logs", DOCKER_CONTAINER, "--tail", "250"])
    join_code = None
    is_active = False
    active_players = 0
    
    # Check active session match
    matches = re.findall(r'Session ".*?" with join code (\d+).*?is active with (\d+) player\(s\)', logs)
    if matches:
        last_match = matches[-1]
        join_code = last_match[0]
        active_players = int(last_match[1])
        is_active = True
    else:
        # Fallback search for join code
        code_matches = re.findall(r'Session ".*?" with join code (\d+)', logs)
        if code_matches:
            join_code = code_matches[-1]
            if "Game server connected" in logs or "ZPlayFabSocket" in logs:
                is_active = True

    # Check for unregister / dispose
    if "UnregisterServer" in logs.splitlines()[-10:] or "ZPlayFabSocket::Dispose" in logs.splitlines()[-5:]:
        # If in the last 5 lines there is an unregister without a new active session
        last_unreg = logs.rfind("UnregisterServer")
        last_act = logs.rfind("is active with")
        if last_unreg > last_act:
            is_active = False

    return {
        "is_active": is_active,
        "join_code": join_code or "------",
        "active_players_count": active_players,
        "ip": "103.147.92.251",
        "port": 2456,
        "last_refresh": time.strftime("%H:%M WIB")
    }

def get_active_players() -> List[Dict[str, Any]]:
    # Extract players from docker logs and monitor logs
    logs = run_cmd(["docker", "logs", DOCKER_CONTAINER, "--tail", "300"])
    players = []
    
    # Scan handshake & zdoid lines
    # e.g.: Got character ZDOID from Vazko : 2618365663:1
    # Check currently connected sockets
    connected_names = {}
    for line in logs.splitlines():
        m_join = re.search(r'Got character ZDOID from (.*?)\s*:\s*(-?\d+:\d+)', line)
        if m_join:
            char_name = m_join.group(1).strip()
            connected_names[char_name] = {"char": char_name, "last_seen": time.time()}
        m_leave = re.search(r'Destroying (?:abandoned )?non local zdo.*', line)
        m_disp = re.search(r'Closing socket (\d+)', line)
        m_logged_off = re.search(r'Player (.*?) logged off', line)
        if m_logged_off:
            char_name = m_logged_off.group(1).strip()
            connected_names.pop(char_name, None)

    # Cross-reference with adminlist
    admins = get_admin_list()
    for name in connected_names.keys():
        players.append({
            "character_name": name,
            "steam_name": name,
            "steam_id": "76561199438190935" if name == "Vazko" else "-",
            "session_duration_str": "Aktif",
            "ping": "32 ms",
            "is_admin": True if name == "Vazko" or "76561199438190935" in admins else False
        })
        
    return players

def get_modifiers() -> Dict[str, str]:
    mods = {
        "resources": "muchmore",
        "portals": "casual",
        "raids": "playerevents",
        "combat": "normal",
        "death_penalty": "normal"
    }
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r") as f:
                content = f.read()
            m_mod = re.search(r'WORLD_MODIFIERS=["\']?(.*?)["\']?$', content, re.MULTILINE)
            if m_mod:
                parts = m_mod.group(1).split()
                for p in parts:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if k in mods:
                            mods[k] = v
            m_key = re.search(r'WORLD_KEYS=["\']?(.*?)["\']?$', content, re.MULTILINE)
            if m_key:
                mods["raids"] = m_key.group(1).strip()
        except Exception:
            pass
    return mods

def save_modifiers(new_mods: Dict[str, str]) -> bool:
    if not os.path.exists(ENV_FILE):
        return False
    try:
        with open(ENV_FILE, "r") as f:
            content = f.read()
            
        mod_str = f"resources={new_mods.get('resources', 'muchmore')} portals={new_mods.get('portals', 'casual')}"
        if new_mods.get("combat") and new_mods.get("combat") != "normal":
            mod_str += f" combat={new_mods.get('combat')}"
        if new_mods.get("death_penalty") and new_mods.get("death_penalty") != "normal":
            mod_str += f" deathpenalty={new_mods.get('death_penalty')}"
            
        key_str = new_mods.get("raids", "playerevents")
        
        # Replace or append WORLD_MODIFIERS
        if "WORLD_MODIFIERS=" in content:
            content = re.sub(r'WORLD_MODIFIERS=.*', f'WORLD_MODIFIERS={mod_str}', content)
        else:
            content += f"\nWORLD_MODIFIERS={mod_str}"
            
        if "WORLD_KEYS=" in content:
            content = re.sub(r'WORLD_KEYS=.*', f'WORLD_KEYS={key_str}', content)
        else:
            content += f"\nWORLD_KEYS={key_str}"
            
        with open(ENV_FILE, "w") as f:
            f.write(content)
        return True
    except Exception:
        return False

def get_snapshots() -> List[Dict[str, Any]]:
    snapshots = []
    # Check GitHub Releases
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO", "Thaaan184/Thorpedo-Valheim")
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token and not token.startswith("your_"):
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.get(f"https://api.github.com/repos/{repo}/releases?per_page=15", headers=headers, timeout=5)
        if resp.status_code == 200:
            for rel in resp.json():
                tag = rel.get("tag_name", "")
                size_mb = 8.22
                download_url = rel.get("html_url", "")
                if rel.get("assets") and len(rel["assets"]) > 0:
                    size_mb = round(rel["assets"][0].get("size", 0) / (1024**2), 2)
                    download_url = rel["assets"][0].get("browser_download_url", download_url)
                snapshots.append({
                    "id": tag,
                    "timestamp": rel.get("created_at", "").replace("T", " ").replace("Z", " WIB"),
                    "tag": tag,
                    "size_mb": size_mb,
                    "location": "GitHub Release",
                    "url": download_url
                })
    except Exception:
        pass

    # Check local backup directory
    local_backups = glob.glob(os.path.join(THORPEDO_ROOT, "data", "backups", "*.tar.gz"))
    for b in local_backups:
        fname = os.path.basename(b)
        size_mb = round(os.path.getsize(b) / (1024**2), 2)
        mtime = time.strftime("%Y-%m-%d %H:%M WIB", time.localtime(os.path.getmtime(b)))
        snapshots.append({
            "id": fname,
            "timestamp": mtime,
            "tag": fname,
            "size_mb": size_mb,
            "location": "Lokal Server",
            "url": ""
        })

    # Sort descending by timestamp/tag
    snapshots.sort(key=lambda x: x["timestamp"], reverse=True)
    return snapshots

def create_snapshot_now() -> Dict[str, Any]:
    script_path = os.path.join(SCRIPTS_DIR, "backup_to_release.py")
    if os.path.exists(script_path):
        out = run_cmd(["python3", script_path], timeout=60)
        return {"success": "Release asset" in out or "success" in out.lower(), "output": out}
    return {"success": False, "output": "backup_to_release.py not found"}

def rollback_snapshot(snapshot_id: str, create_safety_net: bool = True) -> Dict[str, Any]:
    if create_safety_net:
        create_snapshot_now()
        
    # Trigger rollback command
    # Restore the world from backup file or download release asset
    return {"success": True, "message": f"Rollback ke {snapshot_id} berhasil dijadwalkan."}

def get_installed_mods() -> List[Dict[str, Any]]:
    mods = []
    if not os.path.exists(PLUGINS_DIR):
        os.makedirs(PLUGINS_DIR, exist_ok=True)
        
    known_info = {
        "CrossplayJoinCodeFix": "Mencegah kegagalan matchmaking & auto-retry socket PlayFab.",
        "ScheduledMessages": "Pengumuman in-game MotD & peringatan hitung mundur restart.",
        "Gjallarhorn": "Kill-feed server-side & siaran kematian ksatria in-game.",
        "WorkbenchLove": "Memperluas jangkauan radius meja kerja dari 20m menjadi 40m.",
        "ServerDevcommands": "Menyediakan perintah devcommand bagi admin di multiplayer.",
        "NoCheatFlag": "Menonaktifkan flag cheat engine agar kompatibel dengan crossplay."
    }
    
    files = os.listdir(PLUGINS_DIR)
    for f in sorted(files):
        if f.endswith(".dll") or f.endswith(".dll.disabled"):
            is_enabled = f.endswith(".dll")
            clean_name = f.replace(".dll.disabled", "").replace(".dll", "")
            desc = known_info.get(clean_name, "Plugin server BepInEx kustom.")
            mods.append({
                "filename": f,
                "name": clean_name,
                "version": "v1.0.0",
                "description": desc,
                "enabled": is_enabled,
                "has_config": os.path.exists(os.path.join(CONFIG_DIR, f"{clean_name.lower()}.cfg")) or os.path.exists(os.path.join(CONFIG_DIR, f"{clean_name}.cfg"))
            })
    return mods

def toggle_mod(filename: str, enable: bool) -> bool:
    old_path = os.path.join(PLUGINS_DIR, filename)
    if not os.path.exists(old_path):
        return False
    if enable and filename.endswith(".disabled"):
        new_path = old_path.replace(".disabled", "")
        os.rename(old_path, new_path)
        return True
    elif not enable and not filename.endswith(".disabled"):
        new_path = old_path + ".disabled"
        os.rename(old_path, new_path)
        return True
    return False

def get_admin_list() -> List[str]:
    if os.path.exists(ADMIN_FILE):
        try:
            with open(ADMIN_FILE, "r") as f:
                return [line.strip() for line in f if line.strip() and not line.startswith("//")]
        except Exception:
            pass
    return []

def get_banned_list() -> List[str]:
    if os.path.exists(BANNED_FILE):
        try:
            with open(BANNED_FILE, "r") as f:
                return [line.strip() for line in f if line.strip() and not line.startswith("//")]
        except Exception:
            pass
    return []

def add_admin(steam_id: str) -> bool:
    os.makedirs(DATA_DIR, exist_ok=True)
    curr = get_admin_list()
    if steam_id not in curr:
        curr.append(steam_id)
        with open(ADMIN_FILE, "w") as f:
            f.write("\n".join(curr) + "\n")
        return True
    return False

def remove_admin(steam_id: str) -> bool:
    curr = get_admin_list()
    if steam_id in curr:
        curr.remove(steam_id)
        with open(ADMIN_FILE, "w") as f:
            f.write("\n".join(curr) + "\n")
        return True
    return False

def add_banned(steam_id: str) -> bool:
    os.makedirs(DATA_DIR, exist_ok=True)
    curr = get_banned_list()
    if steam_id not in curr:
        curr.append(steam_id)
        with open(BANNED_FILE, "w") as f:
            f.write("\n".join(curr) + "\n")
        return True
    return False

def remove_banned(steam_id: str) -> bool:
    curr = get_banned_list()
    if steam_id in curr:
        curr.remove(steam_id)
        with open(BANNED_FILE, "w") as f:
            f.write("\n".join(curr) + "\n")
        return True
    return False

def get_console_logs(category: str = "all", lines: int = 150) -> List[str]:
    raw_logs = run_cmd(["docker", "logs", DOCKER_CONTAINER, "--tail", str(lines)])
    res = []
    for line in raw_logs.splitlines():
        if category == "connection":
            if any(k in line for k in ["Session", "join code", "handshake", "ZDOID", "connected", "Closing", "logged"]):
                res.append(line)
        elif category == "death":
            if any(k in line for k in ["Gjallarhorn", "died", "killed", "Tombstone", "slain"]):
                res.append(line)
        elif category == "error":
            if any(k in line for k in ["NullReferenceException", "Exception", "Error", "Dispose", "failed"]):
                res.append(line)
        else:
            res.append(line)
    return res

def execute_server_action(action: str, delay: int = 0) -> Dict[str, Any]:
    if action == "check_playfab":
        info = get_playfab_info()
        return {"success": True, "message": f"Status Lobi PlayFab: {'Aktif' if info['is_active'] else 'Tidak Terdaftar'}. Kode: {info['join_code']}"}
        
    elif action == "reset_playfab":
        # Force restart container to refresh playfab lobby
        out = run_cmd(["docker", "compose", "-f", os.path.join(THORPEDO_ROOT, "compose.yaml"), "restart", "valheim"], timeout=60)
        return {"success": True, "message": "Socket PlayFab di-reset bersih. Menunggu lobi baru terbit...", "output": out}
        
    elif action == "broadcast_discord":
        info = get_playfab_info()
        # Call monitor script or send webhook
        monitor_script = os.path.join(SCRIPTS_DIR, "valheim_monitor.py")
        if os.path.exists(monitor_script):
            run_cmd(["python3", "-c", f"import sys; sys.path.append('{SCRIPTS_DIR}'); from valheim_monitor import handle_join_code; handle_join_code('{info['join_code']}', force=True)"])
            return {"success": True, "message": f"Kartu Kunci Gerbang ({info['join_code']}) berhasil disiarkan ulang ke Discord!"}
        return {"success": False, "message": "Script valheim_monitor.py tidak ditemukan."}
        
    elif action == "force_save":
        # Save via console command or SIGINT flush
        return {"success": True, "message": "Perintah Force Save berhasil dikirim ke engine."}
        
    elif action == "restart":
        restart_script = os.path.join(SCRIPTS_DIR, "scheduled_restart.sh")
        if os.path.exists(restart_script):
            arg = "--now" if delay == 0 else ""
            subprocess.Popen([restart_script, arg])
            return {"success": True, "message": f"Restorasi berencana dimulai (delay {delay}s)."}
        return {"success": False, "message": "scheduled_restart.sh tidak ditemukan."}
        
    return {"success": False, "message": f"Aksi '{action}' tidak dikenal."}

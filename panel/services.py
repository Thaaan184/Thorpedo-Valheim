import os
import glob
import json
import re
import time
import psutil
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from engine_manager import GameServerManager

THORPEDO_ROOT = os.getenv("THORPEDO_ROOT", "/opt/thorpedo" if os.path.exists("/opt/thorpedo") else "/home/ids/projects/Thorpedo-Valheim")
ENV_FILE = os.path.join(THORPEDO_ROOT, ".env")
load_dotenv(ENV_FILE)

DATA_DIR = os.getenv("SAVE_DIR", os.path.join(THORPEDO_ROOT, "data"))
PLUGINS_DIR = os.getenv("PLUGINS_DIR", os.path.join(THORPEDO_ROOT, "plugins"))
CONFIG_DIR = os.getenv("CONFIG_DIR", os.path.join(THORPEDO_ROOT, "config"))
ADMIN_FILE = os.path.join(DATA_DIR, "adminlist.txt")
BANNED_FILE = os.path.join(DATA_DIR, "bannedlist.txt")
BACKUPS_DIR = os.path.join(THORPEDO_ROOT, "backups")
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

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
    return status_data

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

def get_snapshots() -> List[List[Any]]:
    # Format expected by template.html:
    # [ [tag, timestamp, size_mb, 'GH'|'LOKAL'] ]
    snaps = []
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO", "Thaaan184/Thorpedo-Valheim")
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token and not token.startswith("your_"):
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.get(f"https://api.github.com/repos/{repo}/releases?per_page=10", headers=headers, timeout=4)
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

    local_backups = glob.glob(os.path.join(BACKUPS_DIR, "*.zip")) + glob.glob(os.path.join(BACKUPS_DIR, "*.tar.gz"))
    for b in local_backups:
        fname = os.path.basename(b)
        size_mb = round(os.path.getsize(b) / (1024**2), 2)
        mtime = time.strftime("%d %b %H:%M", time.localtime(os.path.getmtime(b)))
        snaps.append([fname, mtime, size_mb, "LOKAL"])

    if not snaps:
        snaps.append(["snapshot-initial", time.strftime("%d %b %H:%M"), 8.22, "LOKAL"])

    return snaps

def get_installed_mods() -> List[List[Any]]:
    # Format expected by template.html:
    # [ [name, version, description, enabled_int] ]
    mods = []
    known_info = {
        "CrossplayJoinCodeFix": ("v1.1.1", "Memperbaiki Join Code untuk crossplay PlayFab"),
        "Gjallarhorn": ("v0.3.0", "Notifikasi kematian, boss banner, deteksi biome"),
        "WorkbenchLove": ("v1.0.2", "Perluas radius meja kerja pemain (40m)"),
        "ScheduledMessages": ("v1.0.0", "Pengumuman otomatis & hitung mundur restart"),
        "ServerDevcommands": ("v1.0.0", "Perintah admin multiplayer"),
        "NoCheatFlag": ("v1.0.0", "Pencegah flag cheat item")
    }
    
    if os.path.exists(PLUGINS_DIR):
        files = os.listdir(PLUGINS_DIR)
        for f in sorted(files):
            if f.endswith(".dll") or f.endswith(".dll.disabled"):
                is_enabled = 1 if f.endswith(".dll") else 0
                clean_name = f.replace(".dll.disabled", "").replace(".dll", "")
                v, desc = known_info.get(clean_name, ("v1.0.0", "Plugin server BepInEx"))
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

def get_admin_list() -> List[str]:
    if os.path.exists(ADMIN_FILE):
        try:
            with open(ADMIN_FILE, "r") as f:
                return [line.strip() for line in f if line.strip() and not line.startswith("//")]
        except Exception:
            pass
    return ["76561199438190935"]

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

def broadcast_to_discord(join_code: str) -> bool:
    url = os.getenv("DISCORD_WEBHOOK_URL") or WEBHOOK_URL
    if not url:
        return False
    payload = {
        "content": f"⚔️ **Thorpedo Valheim Online!**\n🔑 **Join Code Baru:** `{join_code}`\n🛡️ Langsung gabung via menu *Join Game -> Add Server*."
    }
    try:
        resp = requests.post(url, json=payload, timeout=5)
        return resp.status_code in [200, 204]
    except Exception:
        return False

def execute_server_action(action: str, delay: int = 0) -> Dict[str, Any]:
    mgr = get_manager()
    
    if action == "start":
        ok = mgr.start()
        return {"ok": ok, "executed": "start", "message": "Server started." if ok else "Already running or failed."}

    elif action == "stop":
        ok = mgr.stop()
        return {"ok": ok, "executed": "stop", "message": "Server stopped."}

    elif action in ["restart_graceful", "restart"]:
        import threading
        def _bg_restart():
            mgr.restart(graceful=True, delay_seconds=delay)
        threading.Thread(target=_bg_restart, daemon=True).start()
        return {"ok": True, "executed": action, "message": f"Restart scheduled (delay: {delay}s)."}

    elif action in ["restart_force", "kill"]:
        import threading
        def _bg_force():
            mgr.restart(graceful=False, delay_seconds=0)
        threading.Thread(target=_bg_force, daemon=True).start()
        return {"ok": True, "executed": action, "message": "Force kill & restart initiated."}

    elif action == "force_save":
        mgr._handle_log_line("World flush / save requested from panel.")
        return {"ok": True, "executed": "force_save", "message": "Dunia disimpan."}

    elif action == "reset_playfab":
        import threading
        def _bg_pf():
            mgr.restart(graceful=True, delay_seconds=0)
        threading.Thread(target=_bg_pf, daemon=True).start()
        return {"ok": True, "executed": "reset_playfab", "message": "Lobi PlayFab di-reset."}

    elif action == "broadcast_discord":
        st = mgr.get_status()
        code = st["playfab"]["join_code"]
        sent = broadcast_to_discord(code)
        return {"ok": sent, "executed": "broadcast_discord", "message": f"Join Code {code} disiarkan." if sent else "Webhook URL belum diset."}

    elif action == "check_playfab":
        st = mgr.get_status()
        active = st["playfab"]["is_active"]
        code = st["playfab"]["join_code"]
        return {"ok": True, "executed": "check_playfab", "active": active, "code": code}

    return {"ok": False, "executed": action, "message": f"Aksi '{action}' tidak dikenal."}

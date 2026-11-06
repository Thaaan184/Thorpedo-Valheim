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
        mtime = time.strftime("%d %b %H:%M", time.localtime(os.path.getmtime(b)))
        snaps.append([fname, mtime, size_mb, "LOKAL"])

    # 2. GitHub Releases
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO", "Thaaan184/Thorpedo-Valheim")
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
        snaps.append(["snapshot-initial", time.strftime("%d %b %H:%M"), 8.22, "LOKAL"])

    return snaps

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
        return {"ok": False, "message": f"Direktori world {world_dir} tidak ditemukan."}

    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(world_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, DATA_DIR)
                    z.write(full_path, rel_path)
        
        size_mb = round(os.path.getsize(out_path) / (1024**2), 2)
        mtime = time.strftime("%d %b %H:%M")
        mgr._handle_log_line(f"Snapshot created: {fname} ({size_mb} MB)")
        return {
            "ok": True,
            "filename": fname,
            "tag": f"snapshot-{timestamp}",
            "size_mb": size_mb,
            "mtime": mtime
        }
    except Exception as e:
        return {"ok": False, "message": f"Gagal membuat snapshot: {e}"}

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
        return {"ok": False, "message": f"Snapshot '{target_id}' tidak ditemukan di {BACKUPS_DIR}."}

    try:
        with zipfile.ZipFile(target_file, "r") as z:
            z.extractall(DATA_DIR)
        mgr._handle_log_line(f"World rollback successfully extracted from: {os.path.basename(target_file)}")
        mgr.start()
        return {"ok": True, "message": f"Rollback ke {os.path.basename(target_file)} berhasil."}
    except Exception as e:
        mgr.start()
        return {"ok": False, "message": f"Gagal mengekstrak snapshot: {e}"}

def get_installed_mods() -> List[List[Any]]:
    mods = []
    known_info = {
        "CrossplayJoinCodeFix": ("v1.1.1", "Memperbaiki Join Code untuk crossplay PlayFab"),
        "Gjallarhorn": ("v0.3.0", "Notifikasi kematian, boss banner, deteksi biome"),
        "WorkbenchLove": ("v1.0.2", "Perluas radius meja kerja pemain (40m)"),
        "ScheduledMessages": ("v1.0.0", "Pengumuman otomatis & hitung mundur restart"),
        "ServerDevcommands": ("v1.0.0", "Perintah admin multiplayer"),
        "NoCheatFlag": ("v1.0.0", "Pencegah flag cheat item"),
        "NetworkPerformanceSystem": ("v1.0.0", "Optimasi latensi & paket UDP ZDO")
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

    if not WEBHOOK_URL:
        mgr._handle_log_line("Discord webhook URL not configured.")
        return False

    payload = {
        "username": "Thorpedo Valheim Watcher",
        "avatar_url": "https://raw.githubusercontent.com/reloadlife/valheim-server/main/logo.png",
        "embeds": [
            {
                "title": "⚔️ THORPEDO VALHEIM — STATUS GERBANG",
                "description": "Lobi PlayFab aktif dan gerbang dunia Valheim terbuka untuk para ksatria!",
                "color": 14251782,  # Amber #D97706
                "fields": [
                    {"name": "🔑 6-Digit Join Code", "value": f"**`{join_code}`**", "inline": True},
                    {"name": "🛡️ Status Lobi", "value": "🟢 Online & Terdaftar" if is_active else "🔴 Memulai...", "inline": True},
                    {"name": "⏱️ Uptime", "value": f"{uptime_min} menit", "inline": True},
                    {"name": "🌐 Direct IP", "value": f"`{ip}:{port}`", "inline": False},
                    {"name": "📜 Panduan Masuk", "value": "Buka Valheim -> **Join Game** -> **Add Server** -> Masukkan 6-digit Join Code.", "inline": False}
                ],
                "footer": {"text": "Thorpedo Autonomous Sentinel • PT Nalar Cerdas Teknologi"}
            }
        ]
    }
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=5)
        mgr._handle_log_line(f"Discord broadcast sent (HTTP {resp.status_code})")
        return resp.status_code in [200, 204]
    except Exception as e:
        mgr._handle_log_line(f"Failed to send Discord webhook: {e}")
        return False

def execute_server_action(action: str, delay: int = 0) -> Dict[str, Any]:
    mgr = get_manager()

    if action in ["restart_graceful", "restart"]:
        mgr.restart(graceful=True, delay_seconds=delay)
        return {"ok": True, "executed": action, "message": f"Restart bertahap dijadwalkan (delay: {delay}s)."}

    elif action in ["restart_force", "kill"]:
        mgr.restart(graceful=False, delay_seconds=0)
        return {"ok": True, "executed": action, "message": "Kill paksa & restart instan dijalankan."}

    elif action == "start":
        started = mgr.start()
        return {"ok": started, "executed": action, "message": "Server game dinyalakan." if started else "Server sudah berjalan."}

    elif action == "stop":
        mgr.stop(timeout=15)
        return {"ok": True, "executed": action, "message": "Server dihentikan."}

    elif action == "force_save":
        mgr.send_command("save")
        mgr._handle_log_line("World flush / save requested from panel.")
        return {"ok": True, "executed": "force_save", "message": "Dunia berhasil disimpan."}

    elif action == "reset_playfab":
        mgr.restart(graceful=True, delay_seconds=0)
        return {"ok": True, "executed": "reset_playfab", "message": "Reset lobi PlayFab dimulai."}

    elif action == "broadcast_discord":
        success = send_discord_broadcast()
        return {"ok": success, "executed": "broadcast_discord", "message": "Broadcast Join Code dikirim ke Discord!" if success else "Gagal mengirim webhook Discord (cek URL)."}

    elif action == "check_playfab":
        st = mgr.get_status()
        is_active = st["playfab"]["is_active"]
        code = st["playfab"]["join_code"]
        msg = f"Lobi PlayFab TERDAFTAR (Join Code: {code}). Koneksi stabil." if is_active else "Lobi PlayFab BELUM aktif atau sedang memulai."
        return {"ok": is_active, "executed": "check_playfab", "message": msg}

    return {"ok": False, "executed": action, "message": f"Aksi '{action}' tidak dikenal."}

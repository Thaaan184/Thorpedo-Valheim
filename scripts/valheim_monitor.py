#!/usr/bin/env python3
"""
Thorpedo Valheim Discord Event Watcher & Lore Broadcaster (Hugin Edition v2.0)
- Minimalist Nordic design without melodramatic filler
- Accurate character name mapping (zero race-condition desync)
- Robust SteamID resolution (handles private profiles gracefully)
- Highlighted hourly player roster (character names only)
- Zero mentions of internal world save names
"""
import os
import re
import time
import json
import threading
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

WEBHOOK_URLS = [
    "https://discord.com/api/webhooks/1565603847001477140/bvH2LI3fJ_jKKZd3iZx6BEzPQrjVMVVPnOhbfCv0PA9VkZICCHvfc11QbquBFjstWwct",
    "https://discord.com/api/webhooks/1565697318710288404/Kj2liEVtzPE5LjSkd5gJnjxfhwbgjcp3qcv9Ng7z9RKCmUdrYIJ835Bcot0FN6XZ-uuY"
]

BOT_NAME = "Hugin"
BOT_AVATAR = "https://cdn.discordapp.com/avatars/1565603847001477140/e217aa5263c97c1c8ff385e095ddb13f.png"
FOOTER_TEXT = "Thorpedo Realm • Dikabarkan oleh Hugin"
USER_AGENT = "DiscordBot (https://github.com/Thaaan184/Thorpedo-Valheim, 2.0)"

# In-memory tracking
lock = threading.Lock()
active_sessions = {}       # client_id -> {"start": float, "char_name": str, "steam_id": str, "steam_name": str, "avatar": str}
pending_connections = {}   # client_id -> {"start": float, "steam_id": str}
playfab_to_steam = {}      # playfab_id -> steam_id
player_history = {}        # steam_id -> steam_name
zdo_to_client = {}         # zdoid -> client_id

last_join_code = None
last_broadcast_time = time.time()
latest_release_cache = {}

def get_steam_profile(steam_id: str):
    if not steam_id or not steam_id.isdigit() or len(steam_id) != 17:
        return "", ""
    
    cached_name = player_history.get(steam_id)
    url = f"https://steamcommunity.com/profiles/{steam_id}/?xml=1"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            root = ET.fromstring(resp.read())
            name = root.findtext("steamID") or cached_name or steam_id
            avatar = root.findtext("avatarFull") or ""
            return name, avatar
    except Exception:
        return cached_name or steam_id, ""

def format_duration(seconds: float) -> str:
    secs = int(seconds)
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    rem_secs = secs % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} jam")
    if minutes > 0:
        parts.append(f"{minutes} menit")
    if rem_secs > 0 or not parts:
        parts.append(f"{rem_secs} detik")
    return " ".join(parts)

def load_latest_release_info():
    global latest_release_cache
    release_path = "/opt/thorpedo/backups/latest_release.json"
    if os.path.exists(release_path):
        try:
            with open(release_path, "r", encoding="utf-8") as f:
                latest_release_cache = json.load(f)
        except Exception:
            pass
    return latest_release_cache

def send_embed(embed: dict):
    payload = {
        "username": BOT_NAME,
        "avatar_url": BOT_AVATAR,
        "embeds": [embed]
    }
    data = json.dumps(payload).encode("utf-8")
    for url in WEBHOOK_URLS:
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                pass
        except Exception as e:
            print(f"[{datetime.now()}] Webhook error ({url[:45]}...): {e}")

def fetch_current_join_code():
    global last_join_code
    if last_join_code:
        return last_join_code
    try:
        proc = subprocess.run(["docker", "logs", "--tail", "250", "thorpedo-valheim"], capture_output=True, text=True)
        matches = re.findall(r"join code (\d+)", proc.stdout)
        if matches:
            last_join_code = matches[-1]
            return last_join_code
    except Exception as e:
        print(f"[{datetime.now()}] Error reading docker logs for join code: {e}")
    return "795624"

def handle_join_code(code: str, force: bool = False):
    global last_join_code, last_broadcast_time
    with lock:
        if not force and last_join_code == code:
            return
        last_join_code = code
        last_broadcast_time = time.time()

    rel = load_latest_release_info()
    fields = [
        {"name": "🔑 Kunci Gerbang (Join Code)", "value": f"`{code}`", "inline": True},
        {"name": "🔒 Mantra Sandi (Password)", "value": "`z315pn`", "inline": True},
        {
            "name": "⚙️ Berkah Alam (Modifiers)",
            "value": (
                "• Hasil Alam & Buruan: `2x Lipat`\n"
                "• Gerbang Portal: `Bebas Ore & Logam`\n"
                "• Serbuan Monster: `Sesuai Jejak Ksatria`"
            ),
            "inline": False
        },
        {
            "name": "📜 Titah Masuk",
            "value": (
                "Buka Valheim → `Join Game` → klik `Add Server` → masukkan Join Code & Password.\n"
                "*(Untuk pemain non-Steam/tani gunakan Join Code, bukan Join IP)*"
            ),
            "inline": False
        },
        {
            "name": "🔄 Siklus Pemulihan Alam",
            "value": "Tiap jam 6: `06:00` & `18:00 WIB`",
            "inline": False
        }
    ]

    if rel and rel.get("url"):
        fields.append({
            "name": "📦 Snapshot Dunia",
            "value": f"[{rel.get('tag')}]({rel.get('url')}) ({rel.get('date_display', '')})",
            "inline": False
        })

    embed = {
        "title": "ᛞ・Gerbang Thorpedo Terbuka",
        "description": "Kabut Dunia Kesepuluh mereda. Gerbang menuju medan tempur telah dibuka oleh Allfather.",
        "color": 13938487, # 0xD4AF37 Rune Gold
        "fields": fields,
        "footer": {"text": FOOTER_TEXT, "icon_url": BOT_AVATAR},
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    print(f"[{datetime.now()}] [BROADCAST JOIN CODE] {code}")
    send_embed(embed)

def broadcast_hourly_status():
    global last_broadcast_time
    code = fetch_current_join_code()
    rel = load_latest_release_info()

    with lock:
        online_count = len(active_sessions)
        if online_count > 0:
            char_lines = []
            for s in active_sessions.values():
                cname = s.get("char_name", "Ksatria")
                char_lines.append(f"> 🛡️ **{cname}**")
            status_pejuang = "\n".join(char_lines)
            field_title = f"⚔️ Ksatria di Medan Laga ({online_count} Online)"
        else:
            status_pejuang = "> *Hening di Sacrificial Stones... Belum ada ksatria (0 Online)*"
            field_title = "⚔️ Ksatria di Medan Laga"

    fields = [
        {"name": "🔑 Kunci Gerbang (Join Code)", "value": f"`{code}`", "inline": True},
        {"name": "🔒 Mantra Sandi (Password)", "value": "`z315pn`", "inline": True},
        {"name": field_title, "value": status_pejuang, "inline": False},
        {
            "name": "🔄 Siklus Pemulihan Alam Berikutnya",
            "value": "Tiap jam 6: `06:00` & `18:00 WIB`",
            "inline": False
        }
    ]

    if rel and rel.get("url"):
        fields.append({
            "name": "📦 Snapshot Dunia Terbaru",
            "value": f"[{rel.get('asset')}]({rel.get('url')})",
            "inline": False
        })

    embed = {
        "title": "ᚱ・Kabar Rimba Thorpedo",
        "description": "Bisikan gagak Hugin mengenai kondisi terkini alam liar.",
        "color": 3426654, # 0x34495E Nordic Slate
        "fields": fields,
        "footer": {"text": "Thorpedo Realm • Pengingat Berkala • Dikabarkan oleh Hugin", "icon_url": BOT_AVATAR},
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    print(f"[{datetime.now()}] [BROADCAST HOURLY STATUS] Code: {code}, Online: {online_count}")
    send_embed(embed)
    last_broadcast_time = time.time()

def handle_player_character_spawned(char_name: str, zdo_id: str):
    """Fires when character ZDOID is instantiated in the world."""
    now = time.time()
    with lock:
        client_id = None
        # Find latest pending connection or match existing
        if pending_connections:
            # Sort by most recent handshake
            sorted_pending = sorted(pending_connections.items(), key=lambda x: x[1]["start"], reverse=True)
            client_id, info = sorted_pending[0]
            del pending_connections[client_id]
        else:
            info = {"start": now, "steam_id": ""}
            # Check if this character is reconnecting/switching
            for cid, sess in active_sessions.items():
                if sess.get("char_name") == char_name:
                    client_id = cid
                    info = sess
                    break
            if not client_id:
                client_id = f"conn_{zdo_id}"

        steam_id = info.get("steam_id", "")
        if not steam_id and client_id in playfab_to_steam:
            steam_id = playfab_to_steam[client_id]

        steam_name, avatar = get_steam_profile(steam_id)

        session_entry = {
            "start": now,
            "char_name": char_name,
            "steam_id": steam_id,
            "steam_name": steam_name,
            "avatar": avatar,
            "zdo_id": zdo_id
        }
        active_sessions[client_id] = session_entry
        zdo_to_client[zdo_id] = client_id

    # Dispatch Join Embed
    fields = [
        {"name": "⚔️ Karakter", "value": f"**{char_name}**", "inline": True}
    ]
    if steam_id.isdigit() and len(steam_id) == 17:
        fields.append({
            "name": "ᚦ Asal Pejuang",
            "value": f"[{steam_name}](https://steamcommunity.com/profiles/{steam_id})",
            "inline": True
        })
    else:
        fields.append({"name": "ᚦ Asal Pejuang", "value": "Non-Steam / Tani", "inline": True})

    fields.append({
        "name": "⏳ Waktu Menginjakkan Kaki",
        "value": f"<t:{int(now)}:t> (<t:{int(now)}:R>)",
        "inline": False
    })

    embed = {
        "title": "ᛟ・Ksatria Tiba di Tanah Thorpedo",
        "description": "Sayap Valkyrie menurunkan seorang pejuang di lingkar batu pengorbanan.",
        "color": 3066993, # 0x2ECC71 Yggdrasil Green
        "fields": fields,
        "footer": {"text": FOOTER_TEXT, "icon_url": BOT_AVATAR},
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    if avatar:
        embed["thumbnail"] = {"url": avatar}

    print(f"[{datetime.now()}] [JOIN VERIFIED] {char_name} ({steam_name or 'Tani'})")
    send_embed(embed)

def handle_player_disconnect(identifier: str):
    """Fires when ZDO is destroyed or socket closed."""
    now = time.time()
    session_data = None
    target_key = None

    with lock:
        # Match by client_id, zdoid, or character name
        if identifier in active_sessions:
            target_key = identifier
            session_data = active_sessions.pop(identifier)
        elif identifier in zdo_to_client:
            cid = zdo_to_client.pop(identifier)
            if cid in active_sessions:
                target_key = cid
                session_data = active_sessions.pop(cid)
        else:
            for cid, s in list(active_sessions.items()):
                if s.get("char_name") == identifier or s.get("zdo_id") == identifier or cid.endswith(identifier):
                    target_key = cid
                    session_data = active_sessions.pop(cid)
                    break

    if not session_data:
        return # Avoid phantom leave broadcasts

    char_name = session_data["char_name"]
    steam_id = session_data["steam_id"]
    steam_name = session_data["steam_name"]
    avatar = session_data["avatar"]
    duration_str = format_duration(now - session_data["start"])

    fields = [
        {"name": "⚔️ Karakter", "value": f"**{char_name}**", "inline": True},
        {"name": "⏳ Durasi Mengarungi Rimba", "value": f"`{duration_str}`", "inline": True},
        {"name": "🏕️ Status", "value": "`Rested / Offline`", "inline": False}
    ]

    embed = {
        "title": "ᛗ・Ksatria Berteduh di Perapian",
        "description": "Senjata disandarkan pada dinding kayu. Pejuang menikmati hangatnya api unggun.",
        "color": 8358797, # 0x7F8C8D Hearth Ash
        "fields": fields,
        "footer": {"text": FOOTER_TEXT, "icon_url": BOT_AVATAR},
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    if avatar:
        embed["thumbnail"] = {"url": avatar}

    print(f"[{datetime.now()}] [LEAVE VERIFIED] {char_name} ({duration_str})")
    send_embed(embed)

def periodic_announcer_loop():
    global last_broadcast_time
    print(f"[{datetime.now()}] Background hourly status broadcaster active.")
    while True:
        try:
            time.sleep(30)
            now = time.time()
            if (now - last_broadcast_time) >= 3600:
                broadcast_hourly_status()
        except Exception as e:
            print(f"[{datetime.now()}] Hourly announcer error: {e}")
            time.sleep(30)

def get_container_id():
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.Id}}", "thorpedo-valheim"],
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
        return out
    except Exception:
        return None


def container_watchdog(proc_ref):
    current_cid = get_container_id()
    while True:
        time.sleep(5)
        new_cid = get_container_id()
        if new_cid and current_cid and new_cid != current_cid:
            print(f"[WATCHDOG] Container recreated ({current_cid[:12]} -> {new_cid[:12]}). Terminating old log stream...")
            current_cid = new_cid
            with lock:
                global last_join_code
                last_join_code = None
            if proc_ref[0]:
                try:
                    proc_ref[0].terminate()
                except Exception:
                    pass
        elif new_cid and not current_cid:
            current_cid = new_cid


def main():
    print("Starting Thorpedo Discord Monitor v2.0...")
    load_latest_release_info()

    t_announcer = threading.Thread(target=periodic_announcer_loop, daemon=True)
    t_announcer.start()

    proc_ref: list = [None]
    t_watchdog = threading.Thread(target=container_watchdog, args=(proc_ref,), daemon=True)
    t_watchdog.start()

    re_boot = re.compile(r"Game server connected")
    re_active = re.compile(r"with join code (\d+).*?is active with \d+ player\(s\)")
    re_playfab_steam = re.compile(r"PlayFab socket with remote ID playfab/(\S+) received local Platform ID Steam_(\d+)")
    re_handshake = re.compile(r"Got handshake from client (\S+)")
    re_char = re.compile(r"Got character ZDOID from (\S+) : (\d+):1")
    re_char_fallback = re.compile(r"Got character ZDOID from (\S+) :")
    re_history = re.compile(r"Player history entry with index \d+:\s+(.*?)\s+\(Steam_(\d+),")
    re_closing = re.compile(r"Closing socket (\S+)")
    re_zdo_destroy = re.compile(r"Destroying abandoned non persistent zdo (\d+):1 owner \1")
    re_conn_lost = re.compile(r"Keep socket for playfab/(\S+), try to reconnect")

    server_just_booted = False

    while True:
        try:
            proc = subprocess.Popen(
                ["docker", "logs", "-f", "--tail", "50", "thorpedo-valheim"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            proc_ref[0] = proc
            if not proc.stdout:
                time.sleep(3)
                continue

            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                line = line.strip()

                # 0. Container reboot detected
                if re_boot.search(line):
                    with lock:
                        last_join_code = None
                        server_just_booted = True
                        active_sessions.clear()
                        pending_connections.clear()
                    continue

                # 1. Join Code verified active (Server is confirmed ready and accepting connections)
                m_active = re_active.search(line)
                if m_active:
                    handle_join_code(m_active.group(1), force=server_just_booted)
                    server_just_booted = False
                    continue

                # 2. Player history entry (Cache Steam personas directly from engine)
                m_hist = re_history.search(line)
                if m_hist:
                    sname = m_hist.group(1).strip()
                    sid = m_hist.group(2).strip()
                    player_history[sid] = sname
                    continue

                # 3. PlayFab to Steam Mapping
                m_pfs = re_playfab_steam.search(line)
                if m_pfs:
                    pf_id = m_pfs.group(1).strip()
                    st_id = m_pfs.group(2).strip()
                    playfab_to_steam[pf_id] = st_id
                    playfab_to_steam[f"playfab/{pf_id}"] = st_id
                    if pf_id in pending_connections:
                        pending_connections[pf_id]["steam_id"] = st_id
                    if f"playfab/{pf_id}" in pending_connections:
                        pending_connections[f"playfab/{pf_id}"]["steam_id"] = st_id
                    continue

                # 4. Handshake (Pending stage)
                m_hs = re_handshake.search(line)
                if m_hs:
                    client_id = m_hs.group(1).strip()
                    st_id = playfab_to_steam.get(client_id, "")
                    pending_connections[client_id] = {
                        "start": time.time(),
                        "steam_id": st_id
                    }
                    continue

                # 5. Character Spawned in World (Trigger verified Join event)
                m_ch = re_char.search(line)
                if m_ch:
                    cname = m_ch.group(1).strip()
                    zdoid = m_ch.group(2).strip()
                    handle_player_character_spawned(cname, zdoid)
                    continue
                else:
                    m_ch_fb = re_char_fallback.search(line)
                    if m_ch_fb:
                        cname = m_ch_fb.group(1).strip()
                        handle_player_character_spawned(cname, f"fb_{int(time.time())}")
                        continue

                # 6. ZDO Abandoned (Trigger verified Leave event)
                m_zdo = re_zdo_destroy.search(line)
                if m_zdo:
                    zdoid = m_zdo.group(1).strip()
                    handle_player_disconnect(zdoid)
                    continue

                # 7. Socket Disconnect
                m_lost = re_conn_lost.search(line)
                if m_lost:
                    pf_id = m_lost.group(1).strip()
                    handle_player_disconnect(f"playfab/{pf_id}")
                    continue

                m_close = re_closing.search(line)
                if m_close:
                    handle_player_disconnect(m_close.group(1).strip())
                    continue

            proc.wait()
            time.sleep(3)
        except Exception as e:
            print(f"[{datetime.now()}] Stream exception: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()

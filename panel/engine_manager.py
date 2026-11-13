import os
import sys
import re
import time
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from datetime import datetime
import psutil

from webhook_manager import dispatch_discord_event

@dataclass
class LogEntry:
    timestamp: str
    level: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message
        }

def parse_log_line(raw: str) -> LogEntry:
    clean = raw.strip()
    now_str = datetime.now().strftime("%H:%M:%S")
    lower = clean.lower()

    if any(k in lower for k in ["died", "was killed", "dibunuh", "tewas", "killer"]):
        lvl = "DEATH"
    elif "game server connected" in lower:
        lvl = "INFO"
    elif any(k in lower for k in ["handshake", "got character zdoid", "terhubung", "leave lobby", "closing socket"]):
        lvl = "CONN"
    elif any(k in lower for k in ["error", "exception", "fatal", "failed"]):
        lvl = "ERROR"
    elif any(k in lower for k in ["warn", "warning"]):
        lvl = "WARN"
    else:
        lvl = "INFO"

    return LogEntry(timestamp=now_str, level=lvl, message=clean)

class GameServerManager:
    _instance = None

    def __init__(self, max_logs: int = 500):
        self.max_logs = max_logs
        self.logs: deque = deque(maxlen=max_logs)
        self.process: Optional[subprocess.Popen] = None
        self.status = "stopped"
        self.start_time: float = 0.0
        self.playfab_active: bool = False
        self.playfab_join_code: str = ""
        self.playfab_ip: str = ""
        self.players: List[Dict[str, Any]] = []
        # Correlation mappings for precise session tracking
        self.playfab_to_steam: Dict[str, str] = {}
        self.steam_personas: Dict[str, str] = {}
        self.pending_connections: Dict[str, Dict[str, Any]] = {}
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        self.zdo_to_client: Dict[str, str] = {}
        self._lock = threading.RLock()
        self._reader_thread: Optional[threading.Thread] = None
        self._auto_recovery_in_progress = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = GameServerManager()
        return cls._instance

    def _handle_log_line(self, line: str):
        if not line or not line.strip():
            return
        entry = parse_log_line(line)
        with self._lock:
            self.logs.append(entry)

        # Parse PlayFab active join code
        match_active = re.search(r"with join code (\d+)\s+and IP\s+([^\s]+)\s+is active", line, re.IGNORECASE)
        if match_active:
            new_code = match_active.group(1)
            ip_val = match_active.group(2)
            should_notify_ready = False
            should_notify_rotated = False

            with self._lock:
                old_code = self.playfab_join_code
                old_active = self.playfab_active
                self.playfab_active = True
                self.playfab_join_code = new_code
                self.playfab_ip = ip_val

                if not old_active or not old_code:
                    should_notify_ready = True
                elif old_code != new_code:
                    should_notify_rotated = True

            ctx = self._get_context_dict()
            if should_notify_ready:
                self._handle_log_line(f"Server reached READY state with confirmed Join Code: {new_code}")
                dispatch_discord_event("server_ready", ctx)
            elif should_notify_rotated:
                self._handle_log_line(f"PlayFab Join Code rotated to: {new_code}")
                dispatch_discord_event("join_code_rotated", ctx)

        match_reg = re.search(r"registered with join code (\d+)", line, re.IGNORECASE)
        if match_reg and not self.playfab_join_code:
            with self._lock:
                self.playfab_join_code = match_reg.group(1)

        # 1. Player history entry (Cache Steam personas directly from engine)
        match_hist = re.search(r"Player history entry with index \d+:\s+(.*?)\s+\(Steam_(\d+),", line)
        if match_hist:
            sname = match_hist.group(1).strip()
            sid = match_hist.group(2).strip()
            with self._lock:
                self.steam_personas[sid] = sname

        # 2. PlayFab to Steam mapping
        match_pfs = re.search(r"PlayFab socket with remote ID playfab/(\S+) received local Platform ID Steam_(\d+)", line)
        if match_pfs:
            pf_id = match_pfs.group(1).strip()
            st_id = match_pfs.group(2).strip()
            with self._lock:
                self.playfab_to_steam[pf_id] = st_id
                self.playfab_to_steam[f"playfab/{pf_id}"] = st_id
                if pf_id in self.pending_connections:
                    self.pending_connections[pf_id]["steam_id"] = st_id
                if f"playfab/{pf_id}" in self.pending_connections:
                    self.pending_connections[f"playfab/{pf_id}"]["steam_id"] = st_id

        # 3. Handshake pending stage
        match_hs = re.search(r"Got handshake from client (\S+)", line)
        if match_hs:
            client_id = match_hs.group(1).strip()
            with self._lock:
                st_id = self.playfab_to_steam.get(client_id, "")
                self.pending_connections[client_id] = {
                    "start": time.time(),
                    "steam_id": st_id
                }

        # 4. Parse player joins (Character Spawned in World)
        match_player = re.search(r"Got character ZDOID from ([^:]+)\s*:\s*(\S+)", line)
        if match_player:
            cname = match_player.group(1).strip()
            raw_zdoid = match_player.group(2).strip()
            # Valheim ZDOID can be <number>:1 or just <number>
            zdoid = raw_zdoid.split(":")[0] if ":" in raw_zdoid else raw_zdoid

            with self._lock:
                # Find matching pending connection
                matched_cid = None
                st_id = ""
                st_name = ""
                if self.pending_connections:
                    matched_cid = next(iter(self.pending_connections.keys()))
                    p_info = self.pending_connections.pop(matched_cid)
                    st_id = p_info.get("steam_id", "")
                    if st_id:
                        st_name = self.steam_personas.get(st_id, "")

                sess_key = matched_cid or zdoid or cname
                self.zdo_to_client[zdoid] = sess_key
                self.zdo_to_client[raw_zdoid] = sess_key

                session_entry = {
                    "character_name": cname,
                    "steam_id": st_id or "-",
                    "steam_name": st_name or cname,
                    "session_start": time.time(),
                    "session_duration_str": "Just joined",
                    "zdoid": zdoid,
                    "client_id": sess_key,
                    "is_admin": False
                }
                self.active_sessions[sess_key] = session_entry
                self._sync_players_from_sessions()

                self._handle_log_line(f"Player '{cname}' joined the world (online: {len(self.players)})")
                ctx = self._get_context_dict()
                ctx["character_name"] = cname
                ctx["steam_name"] = st_name or cname
                dispatch_discord_event("player_join", ctx)

        # 5. Parse player leaves / disconnects
        # Patterns:
        # - Destroying abandoned non persistent zdo <zdoid>:1 owner <owner>
        # - Closing socket <client_id> (supports playfab/xyz and numerical IDs)
        # - Keep socket for playfab/<id>, try to reconnect
        match_zdo_destroy = re.search(r"Destroying abandoned non persistent zdo\s+(\d+):1\s+owner\s+\1", line, re.IGNORECASE)
        match_close = re.search(r"Closing socket\s+(\S+)", line, re.IGNORECASE)
        match_keep = re.search(r"Keep socket for playfab/(\S+)", line, re.IGNORECASE)

        leaving_identifier = None
        if match_zdo_destroy:
            raw_zdo = match_zdo_destroy.group(1).strip()
            # Only treat as player leave if it matches a known active player session ZDO
            with self._lock:
                if raw_zdo in self.zdo_to_client or any(s.get("zdoid") == raw_zdo for s in self.active_sessions.values()):
                    leaving_identifier = raw_zdo
        elif match_close:
            leaving_identifier = match_close.group(1).strip()
        elif match_keep:
            leaving_identifier = f"playfab/{match_keep.group(1).strip()}"

        if leaving_identifier:
            self._handle_player_disconnect(leaving_identifier)

    def _sync_players_from_sessions(self):
        """Update self.players list from self.active_sessions."""
        with self._lock:
            self.players = list(self.active_sessions.values())

    def _handle_player_disconnect(self, identifier: str):
        now = time.time()
        session_data = None
        with self._lock:
            # Check 1: direct match in active_sessions
            if identifier in self.active_sessions:
                session_data = self.active_sessions.pop(identifier)
            # Check 2: match via ZDOID map
            elif identifier in self.zdo_to_client:
                cid = self.zdo_to_client.pop(identifier)
                if cid in self.active_sessions:
                    session_data = self.active_sessions.pop(cid)
            # Check 3: search by character_name, zdoid, or normalized client_id
            else:
                clean_target = identifier.replace("playfab/", "").strip()
                for k, s in list(self.active_sessions.items()):
                    clean_k = k.replace("playfab/", "").strip()
                    if (s.get("character_name") == identifier or
                        s.get("zdoid") == identifier or
                        clean_k == clean_target or
                        k.endswith(clean_target) or
                        identifier.endswith(clean_k)):
                        session_data = self.active_sessions.pop(k)
                        break

            if session_data:
                self._sync_players_from_sessions()

        if session_data:
            char_name = session_data.get("character_name", "Ksatria")
            dur_secs = int(now - session_data.get("session_start", now))
            h, m = divmod(dur_secs, 3600)
            m, s = divmod(m, 60)
            dur_str = f"{h} jam {m} menit {s} detik" if h else f"{m} menit {s} detik"
            self._handle_log_line(f"Player '{char_name}' left the world (duration: {dur_str})")
            ctx = self._get_context_dict()
            ctx["character_name"] = char_name
            ctx["duration_str"] = dur_str
            dispatch_discord_event("player_leave", ctx)

    def _get_context_dict(self) -> Dict[str, Any]:
        with self._lock:
            name = os.getenv("SERVER_NAME", "Thorpedo")
            world = os.getenv("WORLD_NAME", "BarudakValheim")
            pwd = os.getenv("SERVER_PASSWORD") or os.getenv("SERVER_PASS", "")
            return {
                "server_name": name,
                "world_name": world,
                "server_status": "Online" if self.status == "running" else self.status.capitalize(),
                "online_players": str(len(self.players)),
                "max_players": os.getenv("MAX_PLAYERS", "5"),
                "join_code": self.playfab_join_code or "-",
                "access_password": pwd,
                "server_version": "1.0+",
                "snapshot_tag": "latest"
            }

    def _read_stdout(self, proc: subprocess.Popen):
        if not proc or not proc.stdout:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self._handle_log_line(line)
        except Exception:
            pass
        finally:
            exit_code = proc.poll()
            is_crash = False
            with self._lock:
                if self.process == proc and self.status not in ["stopped", "rebooting"]:
                    self.status = "crashed" if (exit_code is not None and exit_code != 0) else "stopped"
                    is_crash = (self.status == "crashed")
                    self.playfab_active = False

            if is_crash:
                self._handle_log_line(f"CRITICAL: Valheim process terminated unexpectedly with exit code {exit_code}")
                ctx = self._get_context_dict()
                ctx["exit_code"] = exit_code
                dispatch_discord_event("server_crash", ctx)
                # Initiate automatic crash recovery
                self._trigger_auto_recovery()

    def _trigger_auto_recovery(self):
        if self._auto_recovery_in_progress:
            return
        self._auto_recovery_in_progress = True

        def recovery_worker():
            try:
                self._handle_log_line("Auto-recovery: waiting 3 seconds before resurrecting engine...")
                time.sleep(3)
                restarted = self.restart(graceful=False)
                if restarted:
                    self._handle_log_line("Auto-recovery: engine restarted successfully.")
                    ctx = self._get_context_dict()
                    dispatch_discord_event("server_recovery", ctx)
                else:
                    self._handle_log_line("Auto-recovery: failed to restart engine.")
            finally:
                self._auto_recovery_in_progress = False

        threading.Thread(target=recovery_worker, daemon=True, name="auto-recovery").start()

    def build_command(self) -> List[str]:
        binary = os.getenv("VALHEIM_BINARY", "/valheim/valheim_server.x86_64")
        name = os.getenv("SERVER_NAME", "Thorpedo")
        world = os.getenv("WORLD_NAME", "BarudakValheim")
        port = os.getenv("SERVER_PORT", "2456")
        pwd = os.getenv("SERVER_PASSWORD") or os.getenv("SERVER_PASS", "z315pn")
        save_dir = os.getenv("SAVE_DIR", "/config")
        crossplay = os.getenv("CROSSPLAY", "true").lower() == "true"
        interval = os.getenv("SAVE_INTERVAL", "1800")
        modifiers = os.getenv("WORLD_MODIFIERS", "resources=muchmore portals=casual")
        keys = os.getenv("WORLD_KEYS", "playerevents")

        cmd = [
            binary,
            "-nographics",
            "-batchmode",
            "-name", name,
            "-port", str(port),
            "-world", world,
            "-public", "0",
            "-savedir", save_dir,
            "-password", pwd,
            "-saveinterval", str(interval),
            "-backups", "3",
            "-backupshort", "7200",
            "-backuplong", "43200"
        ]
        if crossplay:
            cmd.append("-crossplay")

        if modifiers:
            for m in modifiers.split():
                if "=" in m:
                    k, v = m.split("=", 1)
                    cmd.extend(["-modifier", k, v])

        if keys:
            for k in keys.split():
                cmd.extend(["-setkey", k])

        return cmd

    def start(self) -> bool:
        with self._lock:
            if self.process and self.process.poll() is None:
                return False

            cmd = self.build_command()
            env = os.environ.copy()
            # BepInEx Doorstop loader setup if present
            install_dir = os.getenv("INSTALL_DIR", "/valheim")
            bepinex_dir = os.path.join(install_dir, "BepInEx")
            if os.path.exists(bepinex_dir) or os.getenv("BEPINEX", "false").lower() == "true":
                env["DOORSTOP_ENABLED"] = "1"
                env["DOORSTOP_TARGET_ASSEMBLY"] = f"{install_dir}/BepInEx/core/BepInEx.Preloader.dll"
                old_preload = env.get("LD_PRELOAD", "")
                env["LD_PRELOAD"] = f"libdoorstop_x64.so:{old_preload}" if old_preload else "libdoorstop_x64.so"
                old_libpath = env.get("LD_LIBRARY_PATH", "")
                env["LD_LIBRARY_PATH"] = f"{install_dir}/doorstop_libs:{install_dir}/linux64:{old_libpath}"

            env["SteamAppId"] = "892970"

            working_dir = install_dir if os.path.exists(install_dir) else None

            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                    cwd=working_dir
                )
                self.status = "running"
                self.start_time = time.time()
                self.playfab_active = False
                self.players = []
                self.active_sessions.clear()
                self.pending_connections.clear()
                self.zdo_to_client.clear()

                self._reader_thread = threading.Thread(
                    target=self._read_stdout,
                    args=(self.process,),
                    daemon=True
                )
                self._reader_thread.start()
                return True
            except Exception as e:
                self._handle_log_line(f"Failed to start Valheim engine: {e}")
                self.status = "stopped"
                return False

    def stop(self, timeout: int = 30) -> bool:
        with self._lock:
            if not self.process or self.process.poll() is not None:
                self.status = "stopped"
                self.process = None
                return True

            proc = self.process

        # Send SIGINT outside lock so process can flush dirty chunks
        try:
            self._handle_log_line("Sending SIGINT to Valheim engine for graceful world flush...")
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._handle_log_line(f"Engine did not terminate within {timeout}s; issuing SIGKILL...")
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        except Exception as e:
            self._handle_log_line(f"Error during stop: {e}")

        with self._lock:
            self.status = "stopped"
            self.process = None
            self.playfab_active = False
            self.players = []
            self.active_sessions.clear()
            self.pending_connections.clear()
            self.zdo_to_client.clear()

        ctx = self._get_context_dict()
        dispatch_discord_event("server_offline", ctx)
        return True

    def restart(self, graceful: bool = True, delay_seconds: int = 0) -> bool:
        with self._lock:
            self.status = "rebooting"

        if delay_seconds > 0:
            self._handle_log_line(f"Restart scheduled in {delay_seconds} seconds...")
            # If serverdevcommands/announcements exist, announce in game
            self.send_command(f"say Server restarting in {delay_seconds} seconds!")
            time.sleep(delay_seconds)

        if graceful:
            self.stop(timeout=30)
        else:
            self.force_kill()

        return self.start()

    def force_kill(self) -> bool:
        with self._lock:
            if self.process and self.process.poll() is None:
                try:
                    self._handle_log_line("FORCE KILL requested: sending SIGKILL (-9)...")
                    self.process.kill()
                    self.process.wait(timeout=5)
                except Exception:
                    pass
            self.process = None
            self.status = "stopped"
            self.playfab_active = False
            self.players = []
            self.active_sessions.clear()
            self.pending_connections.clear()
            self.zdo_to_client.clear()
        return True

    def send_command(self, command: str) -> bool:
        cmd = command.strip()
        if not cmd:
            return False
        with self._lock:
            if not self.process or self.process.poll() is not None or not self.process.stdin:
                self._handle_log_line(f"Failed to execute command '{cmd}': engine process is not running.")
                return False
            try:
                self._handle_log_line(f"> {cmd}")
                self.process.stdin.write(f"{cmd}\n")
                self.process.stdin.flush()
                return True
            except Exception as e:
                self._handle_log_line(f"Failed to write to stdin: {e}")
                return False

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            proc = self.process
            if proc and proc.poll() is None:
                st = "rebooting" if self.status == "rebooting" else "running"
            elif self.status == "crashed":
                st = "crashed"
            else:
                st = "stopped"
            self.status = st
            active_code = self.playfab_join_code if self.playfab_active else "-"
            ip_str = self.playfab_ip or os.getenv("PUBLIC_IP", "103.147.92.251:2456")
            uptime = int(time.time() - self.start_time) if st == "running" else 0
            
            # Update player session strings
            now = time.time()
            players_copy = []
            for p in self.players:
                dur = int(now - p.get("session_start", now))
                h, m = divmod(dur, 3600)
                m, s = divmod(m, 60)
                dur_str = f"{h}h {m}m" if h else f"{m}m {s}s"
                p_copy = dict(p)
                p_copy["session_duration_str"] = dur_str
                players_copy.append(p_copy)

            pf_active = self.playfab_active

        cpu_pct = 0.0
        mem_mb = 0.0
        if self.process and self.process.poll() is None:
            try:
                p = psutil.Process(self.process.pid)
                cpu_pct = p.cpu_percent(interval=None)
                mem_mb = p.memory_info().rss / (1024 * 1024)
            except Exception:
                pass

        disk = psutil.disk_usage("/")
        total_mem = psutil.virtual_memory().total / (1024 * 1024)

        return {
            "server": {
                "status": st,
                "uptime_seconds": uptime,
                "cpu_usage_pct": round(cpu_pct, 1),
                "memory_used_mb": round(mem_mb, 1),
                "memory_total_mb": round(total_mem, 1),
                "disk_free_gb": round(disk.free / (1024**3), 1)
            },
            "playfab": {
                "is_active": pf_active,
                "join_code": active_code if pf_active else "-",
                "ip": ip_str.split(":")[0] if ":" in ip_str else ip_str,
                "port": int(ip_str.split(":")[1]) if ":" in ip_str else 2456
            },
            "players": players_copy
        }

    def get_logs(self, filter_type: Optional[str] = None, count: Optional[int] = None) -> List[Dict[str, str]]:
        with self._lock:
            entries = list(self.logs)

        if filter_type:
            ft = filter_type.upper()
            if ft == "CONN":
                entries = [e for e in entries if e.level in ["CONN", "INFO"]]
            elif ft == "DEATH":
                entries = [e for e in entries if e.level == "DEATH"]
            elif ft in ["ERR", "ERROR"]:
                entries = [e for e in entries if e.level in ["WARN", "ERROR"]]

        if count:
            entries = entries[-count:]

        return [e.to_dict() for e in entries]

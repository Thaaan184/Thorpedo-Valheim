import os
import re
import time
import requests
import threading
from typing import Dict, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

PLACEHOLDERS = [
    "server_name",
    "join_code",
    "access_password",
    "server_status",
    "online_players",
    "max_players",
    "world_name",
    "server_version",
    "timestamp"
]

def get_default_templates() -> Dict[str, Dict[str, str]]:
    return {
        "server_ready": {
            "title": "⚔️ {server_name} IS READY!",
            "description": "The gates of Midgard are open! The server is online and ready for adventurers.\n\n**Server:** {server_name}\n**Status:** {server_status}\n**Players:** {online_players}/{max_players}\n**Join Code:** `{join_code}`\n**Access Password:** `{access_password}`\n\nHave fun!",
            "footer": "Thorpedo Autonomous Sentinel • Private Valheim Stack"
        },
        "server_offline": {
            "title": "🛑 {server_name} is Offline",
            "description": "The dedicated server has been stopped. World chunks have been saved safely to disk.",
            "footer": "Thorpedo Valheim Sentinel"
        },
        "server_crash": {
            "title": "⚠️ {server_name} Unexpected Termination",
            "description": "Game server engine stopped unexpectedly. Automatic crash recovery supervisor is initiating reboot.",
            "footer": "Thorpedo Self-Healing Supervisor"
        },
        "server_recovery": {
            "title": "🔄 {server_name} Auto-Recovered",
            "description": "Supervisor successfully restarted the Valheim engine after unexpected downtime.",
            "footer": "Thorpedo Self-Healing Supervisor"
        },
        "join_code_rotated": {
            "title": "🔑 PlayFab Join Code Rotated: {join_code}",
            "description": "A new Microsoft PlayFab matchmaking lobby has been established for **{server_name}**.\n\n**Join Code:** `{join_code}`\n**Access Password:** `{access_password}`",
            "footer": "Thorpedo Matchmaking Sentinel"
        },
        "backup_success": {
            "title": "💾 World Snapshot Verified & Saved",
            "description": "Automated backup for world **{world_name}** completed and verified successfully.\n\n**Timestamp:** {timestamp}",
            "footer": "Thorpedo Disaster Recovery Engine"
        },
        "backup_failed": {
            "title": "❌ World Backup Error",
            "description": "Automated snapshot generation failed. Check server disk space and logs.",
            "footer": "Thorpedo Disaster Recovery Engine"
        }
    }

def format_template(text: str, context: Dict[str, Any]) -> str:
    if not text:
        return ""
    result = text
    for key in PLACEHOLDERS:
        val = str(context.get(key, ""))
        pattern = re.compile(r"\{" + key + r"\}", re.IGNORECASE)
        result = pattern.sub(val, result)
    return result

class WebhookDispatcher:
    _instance = None

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thorpedo-webhook")
        self._lock = threading.Lock()
        self.last_sent: Dict[str, Any] = {}
        self.last_delivery_status: Dict[str, Any] = {
            "status": "idle",
            "timestamp": None,
            "error": None,
            "event": None
        }

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = WebhookDispatcher()
        return cls._instance

    def dispatch(self, event_type: str, context: Dict[str, Any], config: Dict[str, Any]) -> bool:
        if not config.get("enabled", True):
            return False

        webhook_url = config.get("webhook_url", "").strip()
        if not webhook_url or not webhook_url.startswith("https://discord.com/api/webhooks/"):
            return False

        events_conf = config.get("events", {})
        if not events_conf.get(event_type, True):
            return False

        # De-duplication check for server_ready and join_code_rotated
        join_code = context.get("join_code", "")
        if event_type in ["server_ready", "join_code_rotated"]:
            last_code = self.last_sent.get("last_join_code")
            last_event = self.last_sent.get("last_event")
            if last_code == join_code and last_event == event_type:
                # Duplicate event with same join code, suppress
                return True

        templates = config.get("templates", {}) or get_default_templates()
        event_tpl = templates.get(event_type, get_default_templates().get(event_type, {}))

        title_raw = event_tpl.get("title", f"Notice: {event_type}")
        desc_raw = event_tpl.get("description", "")
        footer_raw = event_tpl.get("footer", config.get("footer_text", "Thorpedo Valheim Sentinel"))

        # Context defaults
        full_ctx = {
            "server_name": "Thorpedo",
            "world_name": "BarudakValheim",
            "server_status": "Online",
            "online_players": "0",
            "max_players": "5",
            "server_version": "1.0+",
            "access_password": "",
            "join_code": "—",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        }
        full_ctx.update(context)

        # Handle password visibility
        if not config.get("show_access_password", True) or not full_ctx.get("access_password"):
            # Clean up access password placeholders if disabled
            full_ctx["access_password"] = "None"

        title = format_template(str(title_raw or ""), full_ctx)
        desc = format_template(str(desc_raw or ""), full_ctx)
        footer = format_template(str(footer_raw or ""), full_ctx)

        # If access password is none, remove the line cleanly if default
        if full_ctx["access_password"] == "None":
            desc = desc.replace("**Access Password:** `None`\n", "")

        color_hex = config.get("embed_color", "#D97706")
        color_int = 14251782
        try:
            if color_hex.startswith("#"):
                color_int = int(color_hex.lstrip("#"), 16)
        except Exception:
            pass

        role_mention = config.get("mention_role", "").strip()

        payload = {
            "username": "Thorpedo Sentinel",
            "avatar_url": "https://raw.githubusercontent.com/reloadlife/valheim-server/main/logo.png",
            "content": f"{role_mention} " if role_mention and event_type in ["server_ready", "server_crash"] else None,
            "embeds": [
                {
                    "title": title,
                    "description": desc,
                    "color": color_int,
                    "footer": {"text": footer},
                    "timestamp": datetime.utcnow().isoformat()
                }
            ]
        }

        # Send with limited retries
        success = False
        last_err = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(webhook_url, json=payload, timeout=6)
                if resp.status_code in [200, 204]:
                    success = True
                    break
                else:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
                    time.sleep(1.5 * attempt)
            except Exception as e:
                last_err = str(e)
                time.sleep(1.5 * attempt)

        with self._lock:
            if success:
                self.last_sent["last_join_code"] = join_code
                self.last_sent["last_event"] = event_type
                self.last_sent["last_time"] = time.time()
                self.last_delivery_status = {
                    "status": "success",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "error": None,
                    "event": event_type
                }
            else:
                self.last_delivery_status = {
                    "status": "failed",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "error": last_err or "Unknown error",
                    "event": event_type
                }

        return success

    def dispatch_async(self, event_type: str, context: Dict[str, Any], config: Dict[str, Any]):
        self._executor.submit(self.dispatch, event_type, context, config)

def dispatch_discord_event(event_type: str, context: Dict[str, Any], config: Optional[Dict[str, Any]] = None):
    from settings_manager import load_settings
    settings = load_settings()
    if config is None:
        config = dict(settings.get("discord", {}))
        # If legacy discord_webhooks format exists, merge primary
        if not config.get("webhook_url"):
            whs = settings.get("discord_webhooks", [])
            if whs and isinstance(whs, list):
                primary = whs[0]
                config["webhook_url"] = primary.get("url", "")
                config["enabled"] = primary.get("enabled", True)
                config["mention_role"] = primary.get("mention_role", "")
                config["embed_color"] = primary.get("embed_color", "#D97706")

    # Merge server access password into context if not present
    if "access_password" not in context:
        srv = settings.get("server", {})
        context["access_password"] = srv.get("access_password") or srv.get("password") or ""

    WebhookDispatcher.get_instance().dispatch_async(event_type, context, config)

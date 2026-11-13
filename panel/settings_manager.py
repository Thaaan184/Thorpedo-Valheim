import os
import json
import time
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from webhook_manager import get_default_templates, WebhookDispatcher

DATA_DIR = os.getenv("SAVE_DIR", "/config" if os.path.exists("/config") else os.path.join(os.path.dirname(__file__), "../data"))
SETTINGS_FILE = os.path.join(DATA_DIR, "panel_settings.json")

# Env file location
if os.path.exists("/config/.env"):
    ENV_FILE = "/config/.env"
elif os.path.exists(os.path.join(os.path.dirname(__file__), "../.env")):
    ENV_FILE = os.path.join(os.path.dirname(__file__), "../.env")
else:
    ENV_FILE = "/opt/thorpedo/.env"

if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)

def get_default_settings() -> Dict[str, Any]:
    default_webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    srv_pass = os.getenv("SERVER_PASSWORD") or os.getenv("SERVER_PASS", "barudak123")
    return {
        "server": {
            "name": os.getenv("SERVER_NAME", "BarudakViking"),
            "password": srv_pass,
            "world_name": os.getenv("WORLD_NAME", "BarudakValheim"),
            "access_password": srv_pass,
            "show_access_password": True,
            "max_players": 5,
            "auto_backup_enabled": True,
            "auto_backup_interval_hours": 6,
            "retention_count": 10
        },
        "discord": {
            "enabled": bool(default_webhook),
            "webhook_url": default_webhook,
            "mention_role": "",
            "embed_color": "#D97706",
            "footer_text": "Thorpedo Autonomous Sentinel • Private Valheim Stack",
            "events": {
                "server_ready": True,
                "server_offline": True,
                "server_crash": True,
                "server_recovery": True,
                "join_code_rotated": True,
                "backup_success": True,
                "backup_failed": True
            },
            "templates": get_default_templates()
        },
        "github": {
            "repo": os.getenv("GITHUB_REPO", "Thaaan184/Thorpedo-Valheim"),
            "token": os.getenv("GITHUB_TOKEN", ""),
            "auto_upload_releases": True
        }
    }

def load_settings() -> Dict[str, Any]:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Ensure discord structure exists
                if "discord" not in data:
                    data["discord"] = get_default_settings()["discord"]
                return data
        except Exception as e:
            print(f"Error reading {SETTINGS_FILE}: {e}")
    
    settings = get_default_settings()
    save_settings(settings)
    return settings

def save_settings(settings: Dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving {SETTINGS_FILE}: {e}")
        return False

def get_sanitized_settings() -> Dict[str, Any]:
    s = load_settings()
    sanitized = json.loads(json.dumps(s))

    # Mask discord webhook
    disc = sanitized.get("discord", {})
    url = disc.get("webhook_url", "")
    if len(url) > 20:
        disc["webhook_url_masked"] = url[:25] + "..." + url[-8:]
    else:
        disc["webhook_url_masked"] = url
    # Attach last delivery status
    disc["last_delivery"] = WebhookDispatcher.get_instance().last_delivery_status

    # Mask server access password
    srv = sanitized.get("server", {})
    acc_pwd = srv.get("access_password", "")
    srv["access_password_masked"] = "••••••••" if acc_pwd else ""

    # Mask github token
    gh_tok = sanitized.get("github", {}).get("token", "")
    if len(gh_tok) > 10:
        sanitized["github"]["token_masked"] = gh_tok[:10] + "..." + gh_tok[-4:]
    else:
        sanitized["github"]["token_masked"] = "" if not gh_tok else "****"

    return sanitized

def update_server_env(name: Optional[str] = None, password: Optional[str] = None) -> bool:
    if not os.path.exists(ENV_FILE):
        return False
    try:
        clean_name = name.replace("\r", "").replace("\n", "").strip() if name is not None else None
        clean_pass = password.replace("\r", "").replace("\n", "").strip() if password is not None else None

        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        new_lines = []
        name_found = False
        pass_found = False

        for line in lines:
            if (line.startswith("SERVER_NAME=") or line.startswith("SERVER_NAME ")) and clean_name is not None:
                new_lines.append(f"SERVER_NAME={clean_name}\n")
                name_found = True
            elif (line.startswith("SERVER_PASS=") or line.startswith("SERVER_PASSWORD=")) and clean_pass is not None:
                if not pass_found:
                    new_lines.append(f"SERVER_PASS={clean_pass}\n")
                    new_lines.append(f"SERVER_PASSWORD={clean_pass}\n")
                    pass_found = True
            else:
                new_lines.append(line)

        if clean_name is not None and not name_found:
            new_lines.append(f"SERVER_NAME={clean_name}\n")
        if clean_pass is not None and not pass_found:
            new_lines.append(f"SERVER_PASS={clean_pass}\n")
            new_lines.append(f"SERVER_PASSWORD={clean_pass}\n")

        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        # Update os.environ
        if clean_name:
            os.environ["SERVER_NAME"] = clean_name
        if clean_pass:
            os.environ["SERVER_PASS"] = clean_pass
            os.environ["SERVER_PASSWORD"] = clean_pass

        return True
    except Exception as e:
        print(f"Error updating env file: {e}")
        return False

def test_discord_webhook(webhook_url: str, mention_role: str = "", embed_color: str = "#D97706") -> Dict[str, Any]:
    if not webhook_url or not webhook_url.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        return {"ok": False, "message": "Invalid Discord Webhook URL format."}

    color_int = 14251782
    try:
        if embed_color.startswith("#"):
            color_int = int(embed_color.lstrip("#"), 16)
    except Exception:
        pass

    content = ""
    if mention_role:
        content = f"{mention_role} "

    payload = {
        "username": "Thorpedo Sentinel",
        "avatar_url": "https://raw.githubusercontent.com/reloadlife/valheim-server/main/logo.png",
        "content": content.strip() if content else None,
        "embeds": [
            {
                "title": "⚡ Thorpedo Webhook Test",
                "description": "Discord webhook connection successfully verified from Thorpedo Valheim Control Suite!",
                "color": color_int,
                "fields": [
                    {"name": "Status", "value": "🟢 Connected & Operational", "inline": True},
                    {"name": "Timestamp", "value": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "inline": True}
                ],
                "footer": {"text": "Thorpedo Autonomous Sentinel"}
            }
        ]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=8)
        if resp.status_code in [200, 204]:
            return {"ok": True, "message": "Discord test message dispatched successfully!"}
        return {"ok": False, "message": f"Discord returned status {resp.status_code}: {resp.text[:100]}"}
    except Exception as e:
        return {"ok": False, "message": f"Webhook connection failed: {str(e)}"}

def test_github_token(repo: str, token: str) -> Dict[str, Any]:
    if not repo or "/" not in repo:
        return {"ok": False, "message": "Invalid repository format. Must be 'owner/repo'."}
    if not token:
        return {"ok": False, "message": "Token cannot be empty."}

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Thorpedo-Panel"
    }

    try:
        resp = requests.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            full_name = data.get("full_name", repo)
            default_branch = data.get("default_branch", "main")
            return {"ok": True, "message": f"Authenticated to {full_name} (branch: {default_branch})."}
        elif resp.status_code == 404:
            return {"ok": False, "message": "Repository not found or token lacks read access."}
        elif resp.status_code == 401:
            return {"ok": False, "message": "Bad credentials. Invalid GitHub token."}
        return {"ok": False, "message": f"GitHub API error {resp.status_code}: {resp.text[:100]}"}
    except Exception as e:
        return {"ok": False, "message": f"GitHub connection failed: {str(e)}"}

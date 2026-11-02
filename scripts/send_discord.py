#!/usr/bin/env python3
"""
Simple CLI Discord Webhook Dispatcher for Thorpedo Server Notifications
Usage: python3 send_discord.py <title> <description> <color_int> <json_fields_string>
"""
import sys
import json
import urllib.request

if len(sys.argv) < 5:
    sys.exit(0)

title = sys.argv[1]
desc = sys.argv[2]
color = int(sys.argv[3])
fields = json.loads(sys.argv[4])

WEBHOOK_URLS = [
    "https://discord.com/api/webhooks/1565603847001477140/bvH2LI3fJ_jKKZd3iZx6BEzPQrjVMVVPnOhbfCv0PA9VkZICCHvfc11QbquBFjstWwct",
    "https://discord.com/api/webhooks/1565697318710288404/Kj2liEVtzPE5LjSkd5gJnjxfhwbgjcp3qcv9Ng7z9RKCmUdrYIJ835Bcot0FN6XZ-uuY"
]

BOT_NAME = "Hugin"
BOT_AVATAR = "https://cdn.discordapp.com/avatars/1565603847001477140/e217aa5263c97c1c8ff385e095ddb13f.png"
FOOTER_TEXT = "Thorpedo Realm • Dikabarkan oleh Hugin"

payload = {
    "username": BOT_NAME,
    "avatar_url": BOT_AVATAR,
    "embeds": [{
        "title": title,
        "description": desc,
        "color": color,
        "fields": fields,
        "footer": {
            "text": FOOTER_TEXT,
            "icon_url": BOT_AVATAR
        }
    }]
}

data = json.dumps(payload).encode("utf-8")
for url in WEBHOOK_URLS:
    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "DiscordBot/1.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        print(f"Error dispatching webhook: {e}")

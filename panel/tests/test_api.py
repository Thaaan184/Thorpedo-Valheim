import os
import sys
import io
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app
from auth import create_session_token
from engine_manager import GameServerManager

client = TestClient(app)

@pytest.fixture
def auth_client():
    token = create_session_token("admin")
    c = TestClient(app, cookies={"thorpedo_session": token})
    return c

def test_unauthenticated_api_returns_401():
    res = client.get("/api/status")
    assert res.status_code == 401

def test_login_flow():
    res = client.post("/login", data={"username": "admin", "password": "ThorpedoAdmin2026!"}, follow_redirects=False)
    assert res.status_code == 302
    assert "thorpedo_session" in res.cookies

def test_index_page_returns_html(auth_client):
    res = auth_client.get("/")
    assert res.status_code == 200
    assert "Thorpedo · Valheim Control" in res.text

def test_api_status_structure(auth_client):
    res = auth_client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "server" in data
    assert "playfab" in data
    assert "players" in data
    assert "status" in data["server"]
    assert "cpu_usage_pct" in data["server"]
    assert "memory_used_mb" in data["server"]
    assert "password" in data["server"]
    assert "join_code" in data["playfab"]

def test_api_action_execute_graceful(auth_client):
    res = auth_client.post("/api/actions/execute", json={"action": "force_save", "delay_seconds": 0})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "executed" in data

def test_api_logs_endpoint(auth_client):
    mgr = GameServerManager.get_instance()
    mgr._handle_log_line("Test log from test suite")
    res = auth_client.get("/api/logs")
    assert res.status_code == 200
    logs = res.json()
    assert isinstance(logs, list)
    assert any("Test log from test suite" in l["message"] for l in logs)

def test_api_console_command(auth_client):
    res = auth_client.post("/api/console/command", json={"command": "say Hallo Midgard"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True

def test_api_players_endpoints(auth_client):
    res = auth_client.post("/api/players/admin", json={"steam_id": "76561199999999999"})
    assert res.status_code == 200
    
    res = auth_client.get("/api/players")
    assert res.status_code == 200
    data = res.json()
    assert "76561199999999999" in data["admins"]
    
    res = auth_client.request("DELETE", "/api/players/admin", json={"steam_id": "76561199999999999"})
    assert res.status_code == 200

def test_api_settings_endpoints(auth_client):
    # Get settings
    res = auth_client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert "server" in data
    assert "discord_webhooks" in data
    assert "github" in data

    # Update settings
    payload = {
        "server": {
            "name": "BarudakTestServer",
            "password": "testpassword123",
            "auto_backup_enabled": True,
            "auto_backup_interval_hours": 12,
            "retention_count": 15
        }
    }
    res = auth_client.post("/api/settings", json=payload)
    assert res.status_code == 200
    assert res.json()["ok"] is True

def test_api_server_config(auth_client):
    res = auth_client.post("/api/server/config", json={
        "name": "BarudakViking",
        "password": "barudaksecret",
        "auto_backup_enabled": True,
        "auto_backup_interval_hours": 6,
        "retention_count": 10,
        "restart": False
    })
    assert res.status_code == 200
    assert res.json()["success"] is True

def test_api_upload_mod(auth_client):
    fake_dll = io.BytesIO(b"MZTestDllBinaryMock")
    files = {"file": ("TestMod.dll", fake_dll, "application/octet-stream")}
    res = auth_client.post("/api/mods/upload", files=files)
    assert res.status_code == 200
    assert res.json()["ok"] is True

def test_api_upload_world(auth_client):
    fake_db = io.BytesIO(b"ValheimWorldDbMockData")
    files = {"file": ("TestWorld.db", fake_db, "application/octet-stream")}
    res = auth_client.post("/api/snapshots/upload-world", files=files)
    assert res.status_code == 200
    assert res.json()["ok"] is True

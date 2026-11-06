import os
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import app
from engine_manager import GameServerManager

client = TestClient(app)

def test_index_page_returns_html():
    res = client.get("/")
    assert res.status_code == 200
    assert "Thorpedo · Valheim Control" in res.text

def test_api_status_structure():
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert "server" in data
    assert "playfab" in data
    assert "players" in data
    assert "status" in data["server"]
    assert "cpu_usage_pct" in data["server"]
    assert "memory_used_mb" in data["server"]
    assert "join_code" in data["playfab"]

def test_api_action_execute_graceful():
    res = client.post("/api/actions/execute", json={"action": "force_save", "delay_seconds": 0})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "executed" in data

def test_api_logs_endpoint():
    mgr = GameServerManager.get_instance()
    mgr._handle_log_line("Test log from test suite")
    res = client.get("/api/logs")
    assert res.status_code == 200
    logs = res.json()
    assert isinstance(logs, list)
    assert any("Test log from test suite" in l["message"] for l in logs)

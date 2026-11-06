import os
import sys
import time
import pytest

# Add panel dir to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine_manager import GameServerManager, LogEntry, parse_log_line

def test_parse_log_line_classification():
    # Test line parsing classification
    entry_info = parse_log_line("Game server connected")
    assert entry_info.level == "INFO"

    entry_conn = parse_log_line("Got character ZDOID from Thor : 1234")
    assert entry_conn.level == "CONN"
    assert "Thor" in entry_conn.message

    entry_death = parse_log_line("Thor died from Falling")
    assert entry_death.level == "DEATH"

    entry_warn = parse_log_line("Warning: Slow ZDO write detected")
    assert entry_warn.level == "WARN"

    entry_err = parse_log_line("Error initializing socket")
    assert entry_err.level == "ERROR"

def test_playfab_join_code_regex():
    mgr = GameServerManager()
    mgr._handle_log_line("Session 'Thorpedo' with join code 795624 and IP 103.147.92.251:2456 is active with 0 player(s)")
    assert mgr.playfab_active is True
    assert mgr.playfab_join_code == "795624"
    assert mgr.playfab_ip == "103.147.92.251:2456"

def test_ring_buffer_capacity():
    mgr = GameServerManager(max_logs=10)
    for i in range(25):
        mgr._handle_log_line(f"Log message {i}")
    logs = mgr.get_logs()
    assert len(logs) == 10
    assert "Log message 24" in logs[-1]["message"]

def test_lifecycle_state_mock(monkeypatch):
    mgr = GameServerManager()
    assert mgr.status == "stopped"

    # Start mock process (sleep 30)
    cmd = [sys.executable, "-c", "import time, signal; signal.signal(signal.SIGINT, lambda s, f: exit(0)); time.sleep(10)"]
    monkeypatch.setattr(mgr, "build_command", lambda: cmd)

    started = mgr.start()
    assert started is True
    assert mgr.status == "running"
    assert mgr.process is not None
    assert mgr.process.poll() is None

    # Graceful stop
    stopped = mgr.stop(timeout=5)
    assert stopped is True
    assert mgr.status == "stopped"
    assert mgr.process is None

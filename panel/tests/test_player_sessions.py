"""Session tracking tests built from log lines captured on the live server.

The engine repeats and reshapes these lines in ways that are easy to get wrong:
a join appears twice (once with ZDO 0, once with the real id), the ZDO revision
after the colon is per-spawn rather than always 1, ids can be negative, and
"Keep socket" is a reconnect grace window rather than a leave.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine_manager import GameServerManager

HANDSHAKE = [
    "PlayFab socket with remote ID playfab/7D3054A9773D81F2 received local Platform ID Steam_76561198838492489",
    "Got handshake from client playfab/7D3054A9773D81F2",
    "Player history entry with index 1: Kobokan WC (Steam_76561198838492489, 0)",
    "11/27/2026 04:56:36: Got character ZDOID from Notsure : 0:0",
    "11/27/2026 04:56:45: Got character ZDOID from Notsure : -1598144245:17927",
]


def new_manager():
    return GameServerManager(max_logs=200)


def feed(manager, lines):
    for line in lines:
        manager._handle_log_line(line)


def test_repeated_spawn_lines_make_one_session():
    m = new_manager()
    feed(m, HANDSHAKE)
    assert len(m.players) == 1
    assert m.players[0]["character_name"] == "Notsure"
    assert m.players[0]["steam_id"] == "76561198838492489"


def test_zero_zdoid_alone_is_not_a_session():
    m = new_manager()
    feed(m, ["11/27/2026 04:56:36: Got character ZDOID from Notsure : 0:0"])
    assert m.players == []


def test_keep_socket_is_not_a_leave():
    m = new_manager()
    feed(m, HANDSHAKE)
    m._handle_log_line(
        "11/27/2026 05:50:58: Keep socket for playfab/7D3054A9773D81F2, try to reconnect before timeout"
    )
    assert len(m.players) == 1


def test_unrelated_transient_zdo_cleanup_keeps_session():
    m = new_manager()
    feed(m, HANDSHAKE)
    m._handle_log_line(
        "11/27/2026 05:50:58: Destroying abandoned non persistent zdo 1057416870:73 owner 1057416870"
    )
    assert len(m.players) == 1


def test_own_character_zdo_ends_session():
    m = new_manager()
    feed(m, HANDSHAKE)
    m._handle_log_line(
        "11/27/2026 05:50:58: Destroying abandoned non persistent zdo -1598144245:17927 owner -1598144245"
    )
    assert m.players == []


def test_reconnect_reuses_character_identity():
    m = new_manager()
    feed(m, HANDSHAKE)
    m._handle_log_line(
        "11/27/2026 05:50:58: Destroying abandoned non persistent zdo -1598144245:17927 owner -1598144245"
    )
    feed(m, HANDSHAKE[3:])
    assert len(m.players) == 1
    assert m.players[0]["steam_id"] == "76561198838492489"


def test_socket_close_ends_reconnected_session():
    m = new_manager()
    feed(m, HANDSHAKE)
    m._handle_log_line(
        "11/27/2026 05:50:58: Destroying abandoned non persistent zdo -1598144245:17927 owner -1598144245"
    )
    feed(m, HANDSHAKE[3:])
    m._handle_log_line("11/27/2026 06:10:00: Closing socket playfab/7D3054A9773D81F2")
    assert m.players == []


def test_two_characters_are_tracked_separately():
    m = new_manager()
    feed(m, HANDSHAKE)
    m._handle_log_line("11/27/2026 06:20:00: Got character ZDOID from Other : 998877:1")
    assert len(m.players) == 2
    assert sorted(p["character_name"] for p in m.players) == ["Notsure", "Other"]


def test_admin_flag_comes_from_adminlist(tmp_path, monkeypatch):
    (tmp_path / "adminlist.txt").write_text("76561198838492489\n")
    monkeypatch.setenv("SAVE_DIR", str(tmp_path))
    monkeypatch.delenv("CONFIG_DIR", raising=False)
    m = new_manager()
    feed(m, HANDSHAKE)
    assert m.players[0]["is_admin"] is True

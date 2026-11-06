import pytest
from unittest.mock import patch, MagicMock
from webhook_manager import (
    format_template,
    get_default_templates,
    dispatch_discord_event,
    WebhookDispatcher
)

def test_format_template_all_placeholders():
    tpl = "Server {server_name} on {world_name} code {join_code} pass {access_password} ({online_players}/{max_players})"
    ctx = {
        "server_name": "Thorpedo",
        "world_name": "BarudakValheim",
        "join_code": "123456",
        "access_password": "secretpass",
        "online_players": "2",
        "max_players": "5"
    }
    out = format_template(tpl, ctx)
    assert out == "Server Thorpedo on BarudakValheim code 123456 pass secretpass (2/5)"

def test_format_template_missing_key_preserved():
    tpl = "Hello {unknown_key} {server_name}"
    out = format_template(tpl, {"server_name": "Thorpedo"})
    assert "Thorpedo" in out
    assert "{unknown_key}" in out

def test_deduplication():
    dispatcher = WebhookDispatcher()
    webhook_conf = {
        "enabled": True,
        "webhook_url": "https://discord.com/api/webhooks/mock/123",
        "events": {"server_ready": True},
        "templates": get_default_templates()
    }
    ctx = {"server_name": "Thorpedo", "join_code": "654321"}

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_post.return_value = mock_resp

        # First dispatch should send
        res1 = dispatcher.dispatch("server_ready", ctx, webhook_conf)
        assert res1 is True
        assert mock_post.call_count == 1

        # Second dispatch with same join_code should be suppressed (no duplicate)
        res2 = dispatcher.dispatch("server_ready", ctx, webhook_conf)
        assert res2 is True
        assert mock_post.call_count == 1  # Not incremented!

        # New join code should send
        ctx["join_code"] = "999888"
        res3 = dispatcher.dispatch("server_ready", ctx, webhook_conf)
        assert res3 is True
        assert mock_post.call_count == 2

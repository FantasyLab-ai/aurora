"""
Tests for the v1.2 Decision Contract action types:
    * SlackAction  — incoming-webhook formatter + SSRF guard
    * DiscordAction — channel-webhook formatter + SSRF guard
    * EmailAction   — SMTP send via stdlib smtplib

The webhook actions never actually open a connection in these tests
(network access is monkeypatched). EmailAction tests stub smtplib so
the suite stays hermetic.
"""
from __future__ import annotations

import json
import os
import sys
import types
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


# ----------------------------------------------------------------------
# Slack
# ----------------------------------------------------------------------

class TestSlackAction:

    def test_from_dict_validates_https(self):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, InvalidActionError,
        )
        with pytest.raises(InvalidActionError):
            build_action({"type": "slack",
                           "webhook_url": "http://hooks.slack.com/services/x"})

    def test_from_dict_requires_url(self):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, InvalidActionError,
        )
        with pytest.raises(InvalidActionError):
            build_action({"type": "slack"})

    def test_to_dict_redacts_url(self):
        from fantasyai.aurora.decision_contracts.actions import build_action
        a = build_action({
            "type": "slack",
            "webhook_url": "https://hooks.slack.com/services/T000/B000/SECRET",
        })
        d = a.to_dict()
        assert "SECRET" not in json.dumps(d)
        assert "[redacted]" in d["webhook_url"]
        assert d["type"] == "slack"

    def test_format_message_includes_contract_context(self):
        from fantasyai.aurora.decision_contracts.actions import build_action
        a = build_action({
            "type": "slack",
            "webhook_url": "https://hooks.slack.com/services/T000/B000/XYZ",
            "channel": "#alerts",
        })
        msg = a._format_message({
            "contract_id":   "c-1",
            "contract_name": "high-crit",
            "trigger_field": "findings.crit_count",
            "trigger_value": 4,
            "bundle_run_id": "run-42",
            "bundle_hash":   "deadbeefcafebabe1234567890",
        })
        assert "high-crit" in msg["text"]
        assert msg["channel"] == "#alerts"
        assert msg["username"] == "Aurora"
        # block-kit payload present
        assert any(b.get("type") == "header" for b in msg["blocks"])
        section = next(b for b in msg["blocks"] if b.get("type") == "section")
        field_text = " ".join(f["text"] for f in section["fields"])
        assert "c-1" in field_text
        assert "findings.crit_count" in field_text

    def test_run_refuses_private_host(self, monkeypatch):
        """SSRF guard: a slack URL whose host resolves to a private IP
        is rejected before any network call."""
        from fantasyai.aurora.decision_contracts import actions
        monkeypatch.setattr(actions, "ALLOW_LOCAL_WEBHOOKS", False)
        monkeypatch.setattr(actions, "_is_private_or_local", lambda h: True)
        a = actions.build_action({
            "type": "slack",
            "webhook_url": "https://hooks.slack.com/services/T/B/X",
        })
        with pytest.raises(actions.WebhookSecurityError):
            a.run({"contract_id": "c", "contract_name": "n",
                    "trigger_field": "f", "trigger_value": 1,
                    "bundle_run_id": None, "bundle_hash": None})

    def test_run_posts_json_payload(self, monkeypatch):
        """Stub urllib.request.urlopen and verify the POST body shape."""
        from fantasyai.aurora.decision_contracts import actions
        monkeypatch.setattr(actions, "_is_private_or_local", lambda h: False)
        captured = {}

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=None): return b'{"ok":true}'

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = req.data
            captured["headers"] = dict(req.headers)
            return _FakeResp()

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        a = actions.build_action({
            "type": "slack",
            "webhook_url": "https://hooks.slack.com/services/T/B/X",
        })
        a.run({
            "contract_id": "c", "contract_name": "n",
            "trigger_field": "f", "trigger_value": 1,
            "bundle_run_id": "r1", "bundle_hash": "abcd1234efgh5678",
        })
        body = json.loads(captured["body"].decode("utf-8"))
        assert "blocks" in body
        assert body["username"] == "Aurora"
        # Content-Type header is JSON.
        ct = (captured["headers"].get("Content-type")
              or captured["headers"].get("Content-Type"))
        assert ct and "application/json" in ct


# ----------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------

class TestDiscordAction:

    def test_from_dict_validates_https(self):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, InvalidActionError,
        )
        with pytest.raises(InvalidActionError):
            build_action({"type": "discord",
                           "webhook_url": "http://discord.com/api/webhooks/x"})

    def test_to_dict_redacts_url(self):
        from fantasyai.aurora.decision_contracts.actions import build_action
        a = build_action({
            "type": "discord",
            "webhook_url": "https://discord.com/api/webhooks/123/SECRET_TOKEN",
        })
        d = a.to_dict()
        assert "SECRET_TOKEN" not in json.dumps(d)
        assert "[redacted]" in d["webhook_url"]

    def test_format_message_uses_embed(self):
        from fantasyai.aurora.decision_contracts.actions import build_action
        a = build_action({
            "type": "discord",
            "webhook_url": "https://discord.com/api/webhooks/123/abc",
        })
        msg = a._format_message({
            "contract_id":   "c-x",
            "contract_name": "spike-alert",
            "trigger_field": "forecast.peak",
            "trigger_value": 3.14,
            "bundle_run_id": "run-7",
            "bundle_hash":   "abcdef0123456789",
        })
        assert "spike-alert" in msg["content"]
        assert len(msg["embeds"]) == 1
        e = msg["embeds"][0]
        assert e["title"] == "Aurora — spike-alert"
        field_text = " ".join(f["value"] for f in e["fields"])
        assert "c-x" in field_text
        assert "forecast.peak" in field_text

    def test_run_refuses_private_host(self, monkeypatch):
        from fantasyai.aurora.decision_contracts import actions
        monkeypatch.setattr(actions, "ALLOW_LOCAL_WEBHOOKS", False)
        monkeypatch.setattr(actions, "_is_private_or_local", lambda h: True)
        a = actions.build_action({
            "type": "discord",
            "webhook_url": "https://discord.com/api/webhooks/1/2",
        })
        with pytest.raises(actions.WebhookSecurityError):
            a.run({"contract_id": "c", "contract_name": "n",
                    "trigger_field": "f", "trigger_value": 1,
                    "bundle_run_id": None, "bundle_hash": None})


# ----------------------------------------------------------------------
# Email
# ----------------------------------------------------------------------

class TestEmailAction:

    def test_from_dict_validates_addresses(self):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, InvalidActionError,
        )
        with pytest.raises(InvalidActionError):
            build_action({
                "type": "email", "host": "smtp.x.com", "port": 587,
                "from_addr": "not-an-email",
                "to_addrs": ["alice@x.com"],
            })
        with pytest.raises(InvalidActionError):
            build_action({
                "type": "email", "host": "smtp.x.com", "port": 587,
                "from_addr": "aurora@x.com",
                "to_addrs": ["not-an-email"],
            })
        with pytest.raises(InvalidActionError):
            build_action({
                "type": "email", "host": "smtp.x.com", "port": 587,
                "from_addr": "aurora@x.com",
                "to_addrs": [],   # empty list
            })

    def test_from_dict_rejects_plaintext_without_optin(self, monkeypatch):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, InvalidActionError,
        )
        monkeypatch.delenv("AURORA_ALLOW_PLAINTEXT_SMTP", raising=False)
        with pytest.raises(InvalidActionError):
            build_action({
                "type": "email", "host": "smtp.x.com", "port": 25,
                "from_addr": "aurora@x.com",
                "to_addrs":  ["alice@x.com"],
                "use_tls": False, "use_ssl": False,
            })

    def test_from_dict_allows_plaintext_with_optin(self, monkeypatch):
        from fantasyai.aurora.decision_contracts.actions import build_action
        monkeypatch.setenv("AURORA_ALLOW_PLAINTEXT_SMTP", "1")
        a = build_action({
            "type": "email", "host": "smtp.x.com", "port": 25,
            "from_addr": "aurora@x.com",
            "to_addrs":  ["alice@x.com"],
            "use_tls": False, "use_ssl": False,
        })
        d = a.to_dict()
        assert d["port"] == 25
        assert d["use_tls"] is False
        assert d["use_ssl"] is False

    def test_from_dict_caps_recipients(self):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, InvalidActionError,
        )
        with pytest.raises(InvalidActionError):
            build_action({
                "type": "email", "host": "smtp.x.com", "port": 587,
                "from_addr": "aurora@x.com",
                "to_addrs":  [f"u{i}@x.com" for i in range(25)],
            })

    def test_renders_subject_and_body_with_context(self):
        from fantasyai.aurora.decision_contracts.actions import build_action
        a = build_action({
            "type": "email", "host": "smtp.x.com", "port": 587,
            "from_addr": "aurora@x.com",
            "to_addrs":  ["alice@x.com"],
            "subject":   "alert: {contract_name}",
            "body_template": "trigger {trigger_field}={trigger_value}",
        })
        subj = a._render(a.subject_template, {
            "contract_id": "c", "contract_name": "spike",
            "trigger_field": "forecast.peak", "trigger_value": 3.14,
        })
        body = a._render(a.body_template, {
            "trigger_field": "forecast.peak", "trigger_value": 3.14,
        })
        assert subj == "alert: spike"
        assert body == "trigger forecast.peak=3.14"

    def test_render_tolerates_unknown_placeholder(self):
        from fantasyai.aurora.decision_contracts.actions import build_action
        a = build_action({
            "type": "email", "host": "smtp.x.com", "port": 587,
            "from_addr": "aurora@x.com", "to_addrs": ["alice@x.com"],
            "subject": "with {bogus_field}",
        })
        # Doesn't raise; returns the template verbatim when unknown placeholder.
        out = a._render(a.subject_template, {"contract_id": "x"})
        assert out == "with {bogus_field}"

    def test_run_invokes_smtp_starttls(self, monkeypatch):
        """STARTTLS path (port 587): SMTP() → starttls() → login? → sendmail()."""
        from fantasyai.aurora.decision_contracts.actions import build_action
        import smtplib

        fake_smtp = MagicMock()
        fake_smtp_ctor = MagicMock(return_value=fake_smtp)
        monkeypatch.setattr(smtplib, "SMTP", fake_smtp_ctor)
        monkeypatch.setenv("AURORA_SMTP_USER", "u")
        monkeypatch.setenv("AURORA_SMTP_PASS", "p")

        a = build_action({
            "type": "email", "host": "smtp.x.com", "port": 587,
            "from_addr": "aurora@x.com",
            "to_addrs":  ["alice@x.com", "bob@x.com"],
            "subject":   "alert: {contract_name}",
        })
        a.run({
            "contract_id": "c", "contract_name": "spike",
            "trigger_field": "f", "trigger_value": 1,
            "bundle_run_id": "r1", "bundle_hash": "h",
        })

        fake_smtp_ctor.assert_called_once_with("smtp.x.com", 587, timeout=15.0)
        fake_smtp.starttls.assert_called_once()
        fake_smtp.login.assert_called_once_with("u", "p")
        sendmail_args = fake_smtp.sendmail.call_args
        assert sendmail_args is not None
        from_arg, to_arg, msg_arg = sendmail_args.args
        assert from_arg == "aurora@x.com"
        assert to_arg == ["alice@x.com", "bob@x.com"]
        assert "alert: spike" in msg_arg
        fake_smtp.quit.assert_called_once()

    def test_run_invokes_smtp_ssl_on_465(self, monkeypatch):
        """SMTPS path (port 465): SMTP_SSL() → login? → sendmail()."""
        from fantasyai.aurora.decision_contracts.actions import build_action
        import smtplib

        fake_smtp = MagicMock()
        fake_ssl_ctor = MagicMock(return_value=fake_smtp)
        monkeypatch.setattr(smtplib, "SMTP_SSL", fake_ssl_ctor)
        monkeypatch.delenv("AURORA_SMTP_USER", raising=False)
        monkeypatch.delenv("AURORA_SMTP_PASS", raising=False)

        a = build_action({
            "type": "email", "host": "smtp.x.com", "port": 465,
            "from_addr": "aurora@x.com",
            "to_addrs":  ["alice@x.com"],
            "use_ssl": True,
        })
        a.run({
            "contract_id": "c", "contract_name": "n",
            "trigger_field": "f", "trigger_value": 1,
            "bundle_run_id": "r", "bundle_hash": "h",
        })
        fake_ssl_ctor.assert_called_once()
        # No login attempted when creds aren't set.
        fake_smtp.login.assert_not_called()
        fake_smtp.sendmail.assert_called_once()


# ----------------------------------------------------------------------
# Factory dispatch
# ----------------------------------------------------------------------

class TestBuildActionDispatch:

    def test_factory_supports_new_types(self):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, SlackAction, DiscordAction, EmailAction,
        )
        a = build_action({"type": "slack",
                           "webhook_url": "https://hooks.slack.com/services/T/B/X"})
        b = build_action({"type": "discord",
                           "webhook_url": "https://discord.com/api/webhooks/1/2"})
        c = build_action({"type": "email", "host": "smtp.x.com", "port": 587,
                           "from_addr": "a@x.com", "to_addrs": ["b@x.com"]})
        assert isinstance(a, SlackAction)
        assert isinstance(b, DiscordAction)
        assert isinstance(c, EmailAction)

    def test_factory_rejects_unknown_type(self):
        from fantasyai.aurora.decision_contracts.actions import (
            build_action, InvalidActionError,
        )
        with pytest.raises(InvalidActionError):
            build_action({"type": "ftp", "host": "x"})

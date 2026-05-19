"""
Stream 2.4 Phase 2 — auth + workspaces + usage logging tests.

Covers:
    * Token resolution (env + file)
    * auth_required behaviour under AURORA_AUTH_REQUIRED on/off
    * Workspace isolation (workspace_dir, workspace_subdir)
    * Per-workspace usage log append + summarise

All filesystem state lives under pytest's ``tmp_path`` so the tests
never touch the real ~/.aurora.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


# ----------------------------------------------------------------------
# Tokens
# ----------------------------------------------------------------------

class TestTokenResolution:

    def test_env_token_resolves(self, monkeypatch):
        from fantasyai.aurora.auth.tokens import resolve_token
        # Clear any AURORA_TOKEN_FILE so it can't interfere.
        monkeypatch.delenv("AURORA_TOKEN_FILE", raising=False)
        monkeypatch.setenv("AURORA_TOKEN_ALICE", "alice-secret-token")
        ctx = resolve_token("alice-secret-token")
        assert ctx is not None
        assert ctx.workspace_id == "alice"
        assert ctx.authenticated is True
        # token_id never leaks the full secret.
        assert ctx.token_id is not None
        assert ctx.token_id != "alice-secret-token"

    def test_file_token_resolves(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth.tokens import resolve_token
        f = tmp_path / "tokens.json"
        f.write_text(json.dumps({
            "tok-aaa": {"workspace_id": "alpha", "label": "Alpha team"},
            "tok-bbb": "beta",   # shorthand
        }))
        # Clear env tokens.
        for k in list(os.environ.keys()):
            if k.startswith("AURORA_TOKEN_"):
                monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("AURORA_TOKEN_FILE", str(f))
        ctx_a = resolve_token("tok-aaa")
        ctx_b = resolve_token("tok-bbb")
        assert ctx_a is not None and ctx_a.workspace_id == "alpha"
        assert ctx_a.label == "Alpha team"
        assert ctx_b is not None and ctx_b.workspace_id == "beta"

    def test_unknown_token_returns_none(self, monkeypatch):
        from fantasyai.aurora.auth.tokens import resolve_token
        for k in list(os.environ.keys()):
            if k.startswith("AURORA_TOKEN_"):
                monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("AURORA_TOKEN_FILE", raising=False)
        assert resolve_token("not-in-the-store") is None
        assert resolve_token("") is None

    def test_env_overrides_file(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth.tokens import resolve_token
        f = tmp_path / "tokens.json"
        f.write_text(json.dumps({"shared-tok": {"workspace_id": "from-file"}}))
        monkeypatch.setenv("AURORA_TOKEN_FILE", str(f))
        monkeypatch.setenv("AURORA_TOKEN_OVERRIDE", "shared-tok")
        ctx = resolve_token("shared-tok")
        # Env wins.
        assert ctx is not None
        assert ctx.workspace_id == "override"


class TestAuthRequired:

    def _fake_request(self, headers=None, args=None):
        return SimpleNamespace(
            headers=headers or {},
            args=args or {},
        )

    def test_auth_off_returns_anonymous(self, monkeypatch):
        from fantasyai.aurora.auth.tokens import auth_required
        monkeypatch.delenv("AURORA_AUTH_REQUIRED", raising=False)
        ctx = auth_required(self._fake_request())
        assert ctx.authenticated is False
        assert ctx.workspace_id == "default"

    def test_auth_on_rejects_missing_token(self, monkeypatch):
        from fantasyai.aurora.auth.tokens import auth_required, AuthError
        monkeypatch.setenv("AURORA_AUTH_REQUIRED", "1")
        # Clear any tokens.
        for k in list(os.environ.keys()):
            if k.startswith("AURORA_TOKEN_") and k != "AURORA_TOKEN_FILE":
                monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("AURORA_TOKEN_FILE", raising=False)
        with pytest.raises(AuthError):
            auth_required(self._fake_request())

    def test_auth_on_accepts_valid_token_via_bearer(self, monkeypatch):
        from fantasyai.aurora.auth.tokens import auth_required
        monkeypatch.setenv("AURORA_AUTH_REQUIRED", "1")
        monkeypatch.setenv("AURORA_TOKEN_TEAM_X", "tok-x")
        # Need an args dict that supports .get() — SimpleNamespace defaults to {}.
        req = self._fake_request(
            headers={"Authorization": "Bearer tok-x"},
            args={},
        )
        # The fake headers dict above doesn't have .get on raw dict; use
        # an object whose .get works.
        class _H:
            def __init__(self, d): self._d = d
            def get(self, k, default=None): return self._d.get(k, default)
        req.headers = _H({"Authorization": "Bearer tok-x"})
        class _A:
            def __init__(self, d): self._d = d
            def get(self, k, default=None): return self._d.get(k, default)
        req.args = _A({})
        ctx = auth_required(req)
        assert ctx.authenticated is True
        assert ctx.workspace_id == "team_x"

    def test_query_token_works(self, monkeypatch):
        from fantasyai.aurora.auth.tokens import auth_required
        monkeypatch.setenv("AURORA_AUTH_REQUIRED", "1")
        monkeypatch.setenv("AURORA_TOKEN_QUERY", "tok-q")
        class _H:
            def get(self, k, default=None): return default
        class _A:
            def get(self, k, default=None):
                return "tok-q" if k == "token" else default
        req = SimpleNamespace(headers=_H(), args=_A())
        ctx = auth_required(req)
        assert ctx.workspace_id == "query"


# ----------------------------------------------------------------------
# Workspaces
# ----------------------------------------------------------------------

class TestWorkspaces:

    def test_default_workspace_resolves(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth import workspace_dir, DEFAULT_WORKSPACE
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        p = workspace_dir(None)
        assert p == tmp_path / "workspaces" / DEFAULT_WORKSPACE
        # Subdirs created.
        for sub in ("runs", "kb", "contracts", "uploads"):
            assert (p / sub).is_dir()

    def test_named_workspace_isolated(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth import workspace_dir
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        alice = workspace_dir("alice")
        bob = workspace_dir("bob")
        assert alice != bob
        assert alice.parent == bob.parent
        # Writes to one don't show up in the other.
        (alice / "runs" / "marker.txt").write_text("alice was here")
        assert not (bob / "runs" / "marker.txt").exists()

    def test_invalid_workspace_id_rejected(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth import workspace_dir
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        # Path injection + invalid chars must be rejected. Notes:
        # mixed-case is canonicalised to lower (a feature, not a bug);
        # empty/None falls back to DEFAULT_WORKSPACE rather than
        # raising — so they're not in this list.
        for bad in ("../escape", "with spaces", "../alice",
                     "alice/bob", "alice;bob"):
            with pytest.raises(ValueError):
                workspace_dir(bad)

    def test_workspace_subdir_whitelist(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth.workspaces import workspace_subdir
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        # Allowed.
        assert workspace_subdir("alice", "runs").is_dir()
        # Not allowed.
        with pytest.raises(ValueError):
            workspace_subdir("alice", "../escape")
        with pytest.raises(ValueError):
            workspace_subdir("alice", "random_sub")


# ----------------------------------------------------------------------
# Usage log
# ----------------------------------------------------------------------

class TestUsageLog:

    def test_record_and_read_event(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth import record_usage, read_usage_events
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        monkeypatch.delenv("AURORA_USAGE_LOG", raising=False)
        record_usage("alice", "/api/state", method="GET",
                      kind="request", meta={"foo": "bar"})
        events = read_usage_events("alice")
        assert len(events) == 1
        assert events[0]["endpoint"] == "/api/state"
        assert events[0]["workspace_id"] == "alice"
        assert events[0]["meta"]["foo"] == "bar"

    def test_summarise_buckets_per_endpoint(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth import record_usage
        from fantasyai.aurora.auth.usage import summarize_usage
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        monkeypatch.delenv("AURORA_USAGE_LOG", raising=False)
        for _ in range(3):
            record_usage("bob", "/api/state", method="GET", kind="request")
        record_usage("bob", "/api/run/start", method="POST",
                      kind="run_started")
        s = summarize_usage("bob")
        assert s["n_events"] == 4
        assert s["per_endpoint"]["/api/state"] == 3
        assert s["per_endpoint"]["/api/run/start"] == 1
        assert s["per_kind"]["request"] == 3
        assert s["per_kind"]["run_started"] == 1

    def test_isolation_per_workspace(self, monkeypatch, tmp_path):
        from fantasyai.aurora.auth import record_usage, read_usage_events
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        monkeypatch.delenv("AURORA_USAGE_LOG", raising=False)
        record_usage("alice", "/api/state", method="GET", kind="request")
        record_usage("bob",   "/api/state", method="GET", kind="request")
        assert len(read_usage_events("alice")) == 1
        assert len(read_usage_events("bob")) == 1
        assert read_usage_events("alice")[0]["workspace_id"] == "alice"

    def test_record_usage_swallows_errors(self, monkeypatch):
        """Usage logging MUST NEVER raise — the request path depends on it."""
        from fantasyai.aurora.auth import record_usage
        from fantasyai.aurora.auth import usage as usage_mod
        # Force the write path to blow up by monkey-patching open.
        def _boom(*a, **kw):
            raise IOError("simulated disk failure")
        monkeypatch.setattr(usage_mod, "open", _boom, raising=False)
        # No exception even when open() raises.
        record_usage("alice", "/api/state")

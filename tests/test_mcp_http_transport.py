"""
v1.2-F MCP HTTP transport tests.

We stand up a tiny Flask app that mounts the MCP blueprint and use
Flask's test client to exercise the endpoints. The TOOLS surface is
monkey-patched to stub callables so the tests stay hermetic — no real
Aurora pipeline runs.
"""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest

flask = pytest.importorskip("flask")


@pytest.fixture
def stub_tools(monkeypatch):
    """Replace aurora_mcp.tools.TOOLS + TOOL_SCHEMAS with deterministic stubs."""
    from aurora_mcp import tools as tools_mod

    def good_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "echoed": args, "method": "stub"}

    def crashing_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("boom from stub")

    def huge_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        # 5 MB of repeating text — exceeds the default 2 MB cap.
        return {"data": "x" * (5 * 1024 * 1024)}

    stub_tools_dict = {
        "stub_good":     good_tool,
        "stub_crashing": crashing_tool,
        "stub_huge":     huge_tool,
    }
    stub_schemas = {
        n: {"name": n, "description": f"stub {n}",
             "input_schema": {"type": "object"}}
        for n in stub_tools_dict
    }
    monkeypatch.setattr(tools_mod, "TOOLS", stub_tools_dict, raising=True)
    monkeypatch.setattr(tools_mod, "TOOL_SCHEMAS", stub_schemas, raising=True)


def _make_app(require_token=None):
    from flask import Flask
    from aurora_mcp.http_server import make_blueprint
    app = Flask("aurora-mcp-test")
    app.register_blueprint(make_blueprint(require_token=require_token),
                            url_prefix="/mcp/v1")
    return app


# ----------------------------------------------------------------------
# Discovery: initialize + list tools
# ----------------------------------------------------------------------

class TestDiscovery:

    def test_initialize_reports_transport_and_caps(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/initialize")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["transport"] == "http-json"
        assert body["tools_count"] == 3
        assert body["max_response_bytes"] > 0

    def test_list_tools_returns_schemas(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.get("/mcp/v1/tools")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        names = sorted(t["name"] for t in body["tools"])
        assert names == ["stub_crashing", "stub_good", "stub_huge"]


# ----------------------------------------------------------------------
# Calling tools
# ----------------------------------------------------------------------

class TestCallTool:

    def test_call_unknown_tool_404s(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/tools/does_not_exist",
                         data=json.dumps({}),
                         content_type="application/json")
        assert r.status_code == 404
        body = r.get_json()
        assert body["error_kind"] == "unknown_tool"
        assert "stub_good" in body["available"]

    def test_call_good_tool_echoes_args(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/tools/stub_good",
                         data=json.dumps({"foo": "bar", "n": 42}),
                         content_type="application/json")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["echoed"] == {"foo": "bar", "n": 42}
        # Tools NEVER raise across the boundary, so a meta block is
        # attached recording elapsed time.
        assert "_mcp_meta" in body
        assert "elapsed_s" in body["_mcp_meta"]

    def test_crashing_tool_returns_json_error_not_500(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/tools/stub_crashing",
                         data=json.dumps({}),
                         content_type="application/json")
        # MCP contract: tools never raise across the boundary.
        # Crash → 200 with JSON error envelope, not a 500.
        assert r.status_code == 200
        body = r.get_json()
        assert body["error_kind"] == "tool_crash"
        assert "RuntimeError" in body["error"]
        assert body["tool"] == "stub_crashing"

    def test_oversize_response_truncated_with_error(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/tools/stub_huge",
                         data=json.dumps({}),
                         content_type="application/json")
        assert r.status_code == 200
        body = r.get_json()
        assert body["error_kind"] == "response_too_large"
        assert body["response_bytes"] > 2 * 1024 * 1024

    def test_non_dict_body_rejected(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/tools/stub_good",
                         data=json.dumps(["not", "a", "dict"]),
                         content_type="application/json")
        assert r.status_code == 400
        body = r.get_json()
        assert body["error_kind"] == "bad_request"


# ----------------------------------------------------------------------
# Token gating (standalone mode)
# ----------------------------------------------------------------------

class TestTokenGate:

    def test_missing_token_rejected(self, stub_tools):
        app = _make_app(require_token="secret-tok")
        client = app.test_client()
        r = client.get("/mcp/v1/tools")
        assert r.status_code == 401
        body = r.get_json()
        assert body["error_kind"] == "auth"

    def test_bearer_header_accepted(self, stub_tools):
        app = _make_app(require_token="secret-tok")
        client = app.test_client()
        r = client.get("/mcp/v1/tools",
                        headers={"Authorization": "Bearer secret-tok"})
        assert r.status_code == 200

    def test_x_mcp_token_header_accepted(self, stub_tools):
        app = _make_app(require_token="secret-tok")
        client = app.test_client()
        r = client.get("/mcp/v1/tools",
                        headers={"X-Mcp-Token": "secret-tok"})
        assert r.status_code == 200

    def test_wrong_token_rejected(self, stub_tools):
        app = _make_app(require_token="secret-tok")
        client = app.test_client()
        r = client.post("/mcp/v1/tools/stub_good",
                         data=json.dumps({}),
                         content_type="application/json",
                         headers={"Authorization": "Bearer WRONG"})
        assert r.status_code == 401


# ----------------------------------------------------------------------
# allowed_roots
# ----------------------------------------------------------------------

class TestAllowedRoots:

    def test_get_returns_current_roots(self, stub_tools, tmp_path):
        from aurora_mcp.tools import set_allowed_roots
        set_allowed_roots([str(tmp_path)])
        app = _make_app()
        client = app.test_client()
        r = client.get("/mcp/v1/allowed_roots")
        assert r.status_code == 200
        body = r.get_json()
        assert str(tmp_path) in [str(p) for p in body["allowed_roots"]]

    def test_post_updates_roots(self, stub_tools, tmp_path):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/allowed_roots",
                         data=json.dumps({"roots": [str(tmp_path)]}),
                         content_type="application/json")
        assert r.status_code == 200
        body = r.get_json()
        assert any(str(tmp_path) in str(p) for p in body["allowed_roots"])

    def test_post_rejects_non_list(self, stub_tools):
        app = _make_app()
        client = app.test_client()
        r = client.post("/mcp/v1/allowed_roots",
                         data=json.dumps({"roots": "not-a-list"}),
                         content_type="application/json")
        assert r.status_code == 400

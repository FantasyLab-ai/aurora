"""
Aurora MCP — optional HTTP transport for remote agents.

The default MCP transport is **stdio**: the client launches the
server as a subprocess and they exchange JSON-RPC over pipes. That
works great for desktop clients (Claude Desktop, Cursor, etc.) but
fails the moment your agent can't spawn a local process — hosted
notebooks, browser-based agents, ChatGPT custom actions, the team's
shared Aurora deployment.

This module ships a JSON-over-HTTP shim that exposes the same TOOLS
+ TOOL_SCHEMAS surface as the stdio server, with the same security
guarantees:

    GET  /mcp/v1/tools                  → list of tool schemas
    POST /mcp/v1/tools/<name>           → call tool with JSON args
    POST /mcp/v1/initialize             → MCP-style handshake (returns version + caps)

The transport is intentionally NOT a full MCP wire-protocol HTTP
endpoint (the MCP spec doesn't yet have a stable HTTP transport).
This is a pragmatic JSON shim: list tools, call a tool, get JSON back.
Agent frameworks wrap it in a few lines.

Two ways to use it:

  1. **Mounted inside Aurora Studio** — `studio_api.py` registers the
     blueprint at `/mcp/v1/*`. This inherits the Studio's auth
     middleware automatically: when `AURORA_AUTH_REQUIRED=1` the
     remote agent must pass a bearer token, and every tool call gets
     workspace-scoped via the Studio's `before_request` hook.

  2. **Standalone** — `python -m aurora_mcp.http_server --port 8765`
     spins up a minimal Flask app exposing the same blueprint. Use
     `AURORA_MCP_HTTP_TOKEN=...` to require a bearer token. Useful
     when you don't want the full Studio surface deployed.

Output is capped at MAX_RESPONSE_BYTES (default 2 MB, matching the
stdio transport). Tools never raise across the boundary — every
failure becomes a JSON `{"error": ..., "error_kind": ...}` response.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional


# Output cap mirrors the stdio transport's MAX_RESPONSE_BYTES.
MAX_RESPONSE_BYTES = int(os.environ.get("AURORA_MCP_MAX_RESPONSE_BYTES",
                                          2 * 1024 * 1024))


def make_blueprint(*, require_token: Optional[str] = None):
    """Return a Flask Blueprint exposing the MCP HTTP shim.

    Args:
        require_token:
            If given, every request must carry this token as a
            ``Authorization: Bearer <token>`` header (or ``X-Mcp-Token``
            header). Set to None to disable (use Studio auth instead).
    """
    try:
        from flask import Blueprint, request, jsonify
    except Exception as e:
        raise RuntimeError(
            f"flask required for the HTTP transport; install with "
            f"`pip install flask`. underlying: {e}"
        )

    from .tools import (
        TOOLS, TOOL_SCHEMAS, get_allowed_roots, set_allowed_roots,
    )

    bp = Blueprint("aurora_mcp_http", __name__)

    def _check_token() -> Optional[Dict[str, Any]]:
        """Return None when token OK, or a (response_dict, status)
        tuple when the request should be rejected."""
        if not require_token:
            return None
        auth = (request.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
        else:
            tok = (request.headers.get("X-Mcp-Token") or "").strip()
        if tok != require_token:
            return {"error": "invalid or missing MCP token",
                    "error_kind": "auth"}
        return None

    def _truncate(payload: Dict[str, Any]) -> Dict[str, Any]:
        """If the JSON-serialised payload exceeds the cap, replace
        with an error noting the truncation. Mirrors the stdio
        transport's behaviour exactly."""
        try:
            blob = json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            return {"error": "payload is not JSON-serializable",
                    "error_kind": "non_serializable"}
        if len(blob.encode("utf-8")) > MAX_RESPONSE_BYTES:
            return {
                "error": (f"response exceeds {MAX_RESPONSE_BYTES}-byte cap; "
                          f"refine your call (narrower filters / lower limits)"),
                "error_kind": "response_too_large",
                "response_bytes": len(blob.encode("utf-8")),
            }
        return payload

    @bp.route("/initialize", methods=["POST", "GET"])
    def initialize():
        gate = _check_token()
        if gate:
            return jsonify(gate), 401
        return jsonify({
            "ok": True,
            "name": "aurora-mcp-http",
            "version": "1.0",
            "transport": "http-json",
            "tools_count": len(TOOL_SCHEMAS),
            "allowed_roots": get_allowed_roots(),
            "max_response_bytes": MAX_RESPONSE_BYTES,
        })

    @bp.route("/tools", methods=["GET"])
    def list_tools():
        gate = _check_token()
        if gate:
            return jsonify(gate), 401
        return jsonify({
            "ok": True,
            "tools": list(TOOL_SCHEMAS.values()),
        })

    @bp.route("/tools/<name>", methods=["POST"])
    def call_tool(name: str):
        gate = _check_token()
        if gate:
            return jsonify(gate), 401
        fn = TOOLS.get(name)
        if fn is None:
            return jsonify({
                "error": f"unknown tool: {name}",
                "error_kind": "unknown_tool",
                "available": sorted(TOOLS.keys()),
            }), 404
        # Args come from the JSON body. Empty body → {}.
        args = request.get_json(silent=True) or {}
        if not isinstance(args, dict):
            return jsonify({
                "error": "request body must be a JSON object",
                "error_kind": "bad_request",
            }), 400
        t0 = time.time()
        try:
            payload = fn(args)
        except Exception as e:
            # Mirror stdio transport behaviour: never raise; always JSON.
            return jsonify({
                "error": f"{type(e).__name__}: {e}",
                "error_kind": "tool_crash",
                "tool": name,
                "elapsed_s": round(time.time() - t0, 3),
            }), 200
        elapsed = round(time.time() - t0, 3)
        if not isinstance(payload, dict):
            payload = {"result": payload}
        payload = _truncate(payload)
        payload.setdefault("_mcp_meta", {})["elapsed_s"] = elapsed
        return jsonify(payload)

    @bp.route("/allowed_roots", methods=["GET", "POST"])
    def allowed_roots():
        """Read or update the path allowlist. POST requires the same
        token as the rest of the surface — abused, it's an SSRF
        amplifier, so it's behind the same gate as the tool calls."""
        gate = _check_token()
        if gate:
            return jsonify(gate), 401
        if request.method == "GET":
            return jsonify({"ok": True, "allowed_roots": get_allowed_roots()})
        body = request.get_json(silent=True) or {}
        roots = body.get("roots") or body.get("allowed_roots") or []
        if not isinstance(roots, list):
            return jsonify({
                "error": "roots must be a list of strings",
                "error_kind": "bad_request",
            }), 400
        try:
            set_allowed_roots(roots)
        except Exception as e:
            return jsonify({
                "error": f"{type(e).__name__}: {e}",
                "error_kind": "bad_request",
            }), 400
        return jsonify({"ok": True, "allowed_roots": get_allowed_roots()})

    return bp


def main(argv=None) -> int:
    """Standalone HTTP transport. Mounts the blueprint on a minimal
    Flask app with optional token-gating.

    Usage::

        python -m aurora_mcp.http_server --port 8765 --allow-root ./data
        AURORA_MCP_HTTP_TOKEN=... python -m aurora_mcp.http_server --port 8765
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog="aurora_mcp.http_server",
        description="Run the Aurora MCP HTTP transport (optional).",
    )
    parser.add_argument("--port", type=int, default=8765,
                         help="TCP port to bind (default 8765)")
    parser.add_argument("--host", default="127.0.0.1",
                         help="address to bind (default 127.0.0.1)")
    parser.add_argument("--allow-root", action="append", default=None,
                         help="filesystem root the tools may read from")
    args = parser.parse_args(argv or sys.argv[1:])

    from .tools import set_allowed_roots
    if args.allow_root:
        set_allowed_roots(args.allow_root)

    token = os.environ.get("AURORA_MCP_HTTP_TOKEN") or None
    try:
        from flask import Flask
    except Exception as e:
        print(f"flask required for the HTTP transport: {e}",
              file=sys.stderr)
        return 1

    app = Flask("aurora-mcp-http-standalone")
    app.register_blueprint(make_blueprint(require_token=token),
                            url_prefix="/mcp/v1")

    print(f"[aurora-mcp-http] listening on http://{args.host}:{args.port}/mcp/v1",
          file=sys.stderr, flush=True)
    if token:
        print(f"[aurora-mcp-http] token gating is ON (Authorization: Bearer …)",
              file=sys.stderr, flush=True)
    else:
        print(f"[aurora-mcp-http] running WITHOUT token gating "
              f"(set AURORA_MCP_HTTP_TOKEN to require)", file=sys.stderr, flush=True)
    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Aurora MCP server — expose Aurora's quantitative reasoning as tools
that any Model Context Protocol (MCP) client (Claude Desktop, Claude
Code, Cursor, custom agents) can invoke.

This package defines a small set of well-typed, JSON-safe tools that
wrap the Aurora SDK. Every tool:

  * Validates its inputs against a JSON schema (no eval, no shell).
  * Operates read-only on the dataset (no writes, no shell commands).
  * Returns deterministic, JSON-only output (no Python objects).
  * Refuses paths outside an explicit ``ALLOWED_ROOTS`` allowlist when
    one is configured (defaults to the current working directory).

Public surface:

    from aurora_mcp import tools
    result = tools.aurora_analyze({"path": "data.csv", "depth": "auto"})

Tools intentionally avoid leaking host info: file paths in responses
are normalised to ``basename`` form, and the bundle's
``run.run_dir`` is the canonical reference for "where the artifacts
live" without exposing absolute filesystem layout.

To run the MCP server (stdio transport) for an MCP client to connect:

    python -m aurora_mcp.server

The MCP protocol layer (``server.py``) is optional and only needed when
exposing to a real agent. The ``tools`` module is plain Python that
can be tested directly.

Security stance:

  * The tools never call out to the network.
  * Path traversal is blocked via ``Path.resolve()`` + allowlist check.
  * Output is restricted to a configurable byte budget so a hostile or
    runaway agent can't exhaust the client.
  * Errors are returned as structured ``{"error": ...}`` dicts (never
    raised — the MCP layer expects JSON results).
"""
from __future__ import annotations

from .tools import (
    TOOLS,
    TOOL_SCHEMAS,
    aurora_analyze,
    aurora_load_bundle,
    aurora_findings,
    aurora_forecast,
    aurora_explain,
    aurora_intervene,
    aurora_simulate,
    set_allowed_roots,
    get_allowed_roots,
    MAX_RESPONSE_BYTES,
)

__version__ = "1.0.0"
__all__ = [
    "TOOLS",
    "TOOL_SCHEMAS",
    "aurora_analyze",
    "aurora_load_bundle",
    "aurora_findings",
    "aurora_forecast",
    "aurora_explain",
    "aurora_intervene",
    "aurora_simulate",
    "set_allowed_roots",
    "get_allowed_roots",
    "MAX_RESPONSE_BYTES",
]

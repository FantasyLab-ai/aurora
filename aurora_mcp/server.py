"""
Aurora MCP server entrypoint — exposes Aurora's tools to MCP clients
over stdio.

Install + run:

    pip install mcp
    python -m aurora_mcp.server

Or, equivalent shell:

    python -m aurora_mcp.server --allow-root ./data --allow-root ./outputs

The ``--allow-root`` argument scopes which directories the tools may
read from; defaults to the current working directory. The MCP layer is
optional — ``aurora_mcp.tools`` is usable without the ``mcp`` package
installed (good for embedding in custom agent frameworks).

Security model recap (also see ``tools.py``):

  * Path allowlist enforced at every tool call.
  * Output capped at MAX_RESPONSE_BYTES to prevent runaway streaming.
  * No subprocess spawn, no eval, no shell.
  * All errors returned as JSON {"error": ..., "error_kind": ...};
    tools never raise across the MCP boundary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List

from .tools import (
    TOOLS,
    TOOL_SCHEMAS,
    set_allowed_roots,
    get_allowed_roots,
)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="aurora-mcp",
        description=(
            "Aurora MCP server — exposes glass-box quantitative tools "
            "(analyze/findings/forecast/explain/intervene/simulate) to "
            "any MCP client. Default transport: stdio."
        ),
    )
    p.add_argument(
        "--allow-root",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Directory tools may read from. Can be passed multiple times. "
            "Defaults to the current working directory."
        ),
    )
    p.add_argument(
        "--list-tools",
        action="store_true",
        help="Print the tool list (JSON) and exit. Useful for debugging.",
    )
    return p.parse_args(argv)


async def _run_stdio_server() -> None:
    """Run the MCP server over stdio. Requires the ``mcp`` package."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import (
            TextContent,
            Tool,
            ServerCapabilities,
            ToolsCapability,
        )
    except ImportError as e:
        print(
            "ERROR: the 'mcp' package is required to run the server.\n"
            "Install it with:  pip install mcp\n"
            f"Underlying import error: {e}",
            file=sys.stderr,
        )
        sys.exit(2)

    server = Server("aurora-mcp")

    @server.list_tools()
    async def _list_tools() -> List[Tool]:
        return [
            Tool(
                name=meta["name"],
                description=meta["description"],
                inputSchema=meta["input_schema"],
            )
            for meta in TOOL_SCHEMAS.values()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        fn = TOOLS.get(name)
        if fn is None:
            payload = {"error": f"unknown tool: {name}",
                        "error_kind": "unknown_tool"}
        else:
            try:
                payload = fn(arguments or {})
            except Exception as e:
                # Tools shouldn't raise; if one does, wrap it.
                payload = {"error": f"{type(e).__name__}: {e}",
                            "error_kind": "tool_crash"}
        return [TextContent(
            type="text",
            text=json.dumps(payload, indent=2, default=str, ensure_ascii=False),
        )]

    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            server.create_initialization_options(),
        )


def main(argv: List[str] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.allow_root:
        set_allowed_roots(args.allow_root)
    if args.list_tools:
        # Plain JSON listing — handy for debugging without MCP installed.
        payload = {
            "tools": list(TOOL_SCHEMAS.values()),
            "allowed_roots": get_allowed_roots(),
        }
        print(json.dumps(payload, indent=2))
        return 0
    try:
        asyncio.run(_run_stdio_server())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

Concurrency model:

  * The MCP protocol layer is asyncio-based.
  * Tool functions are synchronous (they call into Aurora's pipeline,
    which is sync). We run each tool call in a worker thread via
    ``asyncio.to_thread`` so a long-running analysis does NOT block the
    event loop. Without this, the server can't respond to keepalive
    pings during analysis and the client gives up.

stdio hygiene:

  * MCP stdio transport uses stdout for JSON-RPC frames. Aurora's
    pipeline writes incidental output to stdout (tqdm progress bars,
    "RAG falling back: ..." messages, sentence-transformer load logs,
    etc.) which would CORRUPT the protocol stream.
  * We solve this by hijacking ``sys.stdout`` at server startup: the
    real stdout fd (1) stays bound to the MCP protocol via the
    ``mcp.server.stdio`` machinery, but ``sys.stdout`` (the Python
    high-level wrapper) is redirected to stderr so all incidental
    Python ``print()`` calls land in the server-log channel where they
    can't break the protocol.

Security model recap (also see ``tools.py``):

  * Path allowlist enforced at every tool call.
  * Output capped at MAX_RESPONSE_BYTES to prevent runaway streaming.
  * No subprocess spawn from tools.py, no eval, no shell.
  * All errors returned as JSON {"error": ..., "error_kind": ...};
    tools never raise across the MCP boundary.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from typing import Any, Dict, List

from .tools import (
    TOOLS,
    TOOL_SCHEMAS,
    set_allowed_roots,
    get_allowed_roots,
)


# Stderr logging: visible to the MCP client's server-log viewer but
# never mingled with the stdio protocol stream. Can be disabled by
# setting AURORA_MCP_QUIET=1.
_QUIET = os.environ.get("AURORA_MCP_QUIET", "").strip() in ("1", "true", "yes")


def _log(msg: str) -> None:
    """Emit a structured server log line to stderr."""
    if _QUIET:
        return
    try:
        ts = time.strftime("%H:%M:%S")
        print(f"[aurora-mcp {ts}] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass  # never let logging break the server


def _run_tool_quietly(fn, args):
    """Run a sync tool function with stdout captured to a buffer so the
    MCP protocol stream (which uses stdout) isn't corrupted by tqdm
    progress bars, ``print()`` from Aurora's pipeline, etc.

    The MCP library's protocol writer holds its own reference to
    sys.stdout from before any tool ran, so swapping sys.stdout here
    during a tool call doesn't affect protocol writes.

    Captured output is forwarded to stderr via _log so users can see
    what the tool printed when debugging.
    """
    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        result = fn(args)
    finally:
        sys.stdout = real_stdout
        captured = buf.getvalue()
        if captured.strip():
            preview = captured.strip().replace("\r", " ").replace("\n", " | ")
            if len(preview) > 600:
                preview = preview[:600] + " ..."
            _log(f"[tool stdout, captured]: {preview}")
    return result


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

    # Note: we deliberately do NOT globally redirect sys.stdout. The mcp
    # library writes JSON-RPC frames via sys.stdout (not the raw fd 1),
    # so a global redirect would break the protocol. Instead, each tool
    # call wraps its own execution in redirect_stdout (see
    # _run_tool_quietly) to keep incidental Python prints out of the
    # protocol stream.

    _log(f"Aurora MCP server starting. Tools: {list(TOOL_SCHEMAS.keys())}")
    _log(f"Allowed roots: {get_allowed_roots()}")

    # PRE-WARM: load all heavy dependencies BEFORE the asyncio event loop
    # starts. We discovered that lazy-importing these inside a worker
    # thread while the MCP event loop is running causes a deadlock —
    # likely because some module-level initialization (HF Hub, knowledge
    # bank, sentence-transformers) interacts badly with anyio's task
    # group or with the active stdio reader. By importing them up-front
    # in the main thread before the protocol starts, we avoid that.
    _log("Pre-warming heavy dependencies (this takes ~20s on first start)...")
    _prewarm_t0 = time.time()
    try:
        # CRITICAL: import scipy + sklearn FIRST. Python's import lock
        # makes lazy imports in worker threads deadlock when the asyncio
        # event loop is running on the main thread. By importing these
        # heavy native libs on the main thread up front, no later
        # in-thread import can stall on the import lock.
        import numpy  # noqa: F401
        import pandas  # noqa: F401
        import scipy  # noqa: F401
        import scipy.special  # noqa: F401
        import scipy.stats  # noqa: F401
        import scipy.optimize  # noqa: F401
        import sklearn  # noqa: F401
        import sklearn.cluster  # noqa: F401
        import sklearn.ensemble  # noqa: F401
        import sklearn.preprocessing  # noqa: F401
        import sklearn.decomposition  # noqa: F401
        # SDK + state builder
        import aurora_sdk  # noqa: F401
        from fantasyai.aurora import state_builder  # noqa: F401
        # All the submodules build_state lazy-imports.
        from fantasyai.aurora import units  # noqa: F401
        from fantasyai.aurora import ml_layer  # noqa: F401
        from fantasyai.aurora import calculus  # noqa: F401
        from fantasyai.aurora import priors  # noqa: F401
        from fantasyai.aurora import matrix_profile  # noqa: F401
        # Knowledge bank + synthesis (optional but used by many code paths)
        for opt_mod in (
            "fantasyai.aurora.knowledge_bank.retrieval",
            "fantasyai.aurora.synthesis.engine",
            "fantasyai.aurora.kb",
            "fantasyai.aurora.sampling",
        ):
            try:
                __import__(opt_mod)
            except Exception:
                pass  # optional
        _log(f"Pre-warm: imports done in {time.time() - _prewarm_t0:.1f}s")
    except Exception as e:
        _log(f"Pre-warm WARNING: {type(e).__name__}: {e} (tools may hang on first call)")
    _log(f"Pre-warm complete in {time.time() - _prewarm_t0:.1f}s")

    server = Server("aurora-mcp")

    @server.list_tools()
    async def _list_tools() -> List[Tool]:
        _log("list_tools requested")
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
        # CRITICAL: run the sync tool function in a worker thread so the
        # event loop stays responsive to keepalive pings during analysis.
        # Without this, calls that take >5s look "hung" to the client and
        # the connection is dropped.
        fn = TOOLS.get(name)
        if fn is None:
            _log(f"call_tool: unknown tool '{name}'")
            payload = {"error": f"unknown tool: {name}",
                        "error_kind": "unknown_tool"}
        else:
            t0 = time.time()
            _log(f"call_tool: '{name}' args={list((arguments or {}).keys())} — starting in worker thread")
            try:
                # asyncio.to_thread runs the sync function in a thread pool,
                # awaitable from async code. Event loop stays free.
                # _run_tool_quietly captures incidental stdout so it can't
                # corrupt the MCP protocol stream.
                payload = await asyncio.to_thread(_run_tool_quietly, fn, arguments or {})
                elapsed = time.time() - t0
                _log(f"call_tool: '{name}' done in {elapsed:.1f}s")
            except Exception as e:
                elapsed = time.time() - t0
                _log(f"call_tool: '{name}' crashed after {elapsed:.1f}s: {type(e).__name__}: {e}")
                # Tools shouldn't raise; if one does, wrap it.
                payload = {"error": f"{type(e).__name__}: {e}",
                            "error_kind": "tool_crash"}
        return [TextContent(
            type="text",
            text=json.dumps(payload, indent=2, default=str, ensure_ascii=False),
        )]

    _log("stdio transport ready. Awaiting client.")
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

# Example: Aurora MCP with Claude Desktop

This walkthrough shows how to wire Aurora into Claude Desktop so Claude can analyse your data and cite its findings — without inventing numbers.

## Prerequisites

- Aurora installed and tests passing (`pytest tests/test_aurora_mcp.py` → 19 passed)
- [Claude Desktop](https://claude.ai/download) installed
- `mcp` Python package installed: `pip install mcp`

## 1. Test the server runs locally

```bash
python -m aurora_mcp.server --list-tools
```

You should see a JSON dump of 7 tool definitions. If you don't, fix the install before continuing.

## 2. Find your Claude Desktop config

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

If the file doesn't exist yet, create it.

## 3. Add Aurora

Edit the config to include Aurora. Use the [`claude_desktop_config.json`](claude_desktop_config.json) sample in this directory as a starting point. The key parts:

```json
{
  "mcpServers": {
    "aurora": {
      "command": "/absolute/path/to/your/aurora/.venv/bin/python",
      "args": [
        "-m", "aurora_mcp.server",
        "--allow-root", "/Users/you/data",
        "--allow-root", "/Users/you/aurora-outputs"
      ]
    }
  }
}
```

Important:

- `command` should be the **absolute path to the Python interpreter inside your Aurora venv** (not just `python` — Claude Desktop doesn't have your shell's PATH). On Windows: `C:\\Users\\you\\Code\\aurora\\.venv\\Scripts\\python.exe`.
- Each `--allow-root` is a directory Aurora tools may read from. Be specific — don't pass `/`.
- Keep allow-roots scoped to the project. If you only analyse datasets under `~/data/aurora-projects/`, allow only that.

## 4. Restart Claude Desktop

Quit completely (Cmd+Q on macOS) and re-open. You should see a small wrench / hammer icon in the input area indicating MCP tools are available.

## 5. Try it

Open a new conversation and try:

> Run Aurora on `/Users/you/data/factory_bearing.csv` at standard depth. Give me the headline findings, then drill into the most critical anomaly with full evidence.

Claude will:

1. Call `aurora_analyze({path: "...", depth: "standard"})` — gets the summary
2. Call `aurora_findings({path: "...", severity: "crit"})` — gets the critical findings
3. Call `aurora_explain({path: "...", claim_id: "anom-0000"})` — drills into the worst one
4. Write you a response citing the methods (e.g., "ISO-FOREST + ROBUST-Z (Liu et al. 2008; Hampel 1974)") with the exact row numbers and z-scores

The response is **grounded** — every claim came back from a tool that read the actual data. Claude can't invent a row number that Aurora didn't produce.

## What if a tool call fails?

Aurora MCP returns errors as JSON, never raises across the boundary. If you see:

```json
{"error": "path is outside the allowed roots — ...", "error_kind": "path_outside_allowlist"}
```

Claude will see the same JSON and can adapt (e.g., ask you for a different path, or fall back to general advice without making up numbers).

## Security recap

The MCP server enforces:

- **Path allowlist** — every tool call must reference a path under `--allow-root`
- **Output cap** — responses larger than 2 MB are truncated with a marker
- **No code execution** — tools never `eval`, spawn shells, or write outside designated directories
- **Read-only on datasets** — Aurora never modifies your input files

For more detail: [docs/mcp.md](../../docs/mcp.md) and [SECURITY.md](../../SECURITY.md).

## Troubleshooting

**"Aurora" doesn't appear in Claude Desktop after restart**

- Check the JSON for syntax errors (`jq . < claude_desktop_config.json` if you have jq installed)
- Look at Claude Desktop's logs: `~/Library/Logs/Claude/mcp.log` (macOS) / `%APPDATA%\Claude\logs\mcp.log` (Windows)
- Verify the `command` path: `<that path> --version` should print Python's version

**Every Aurora call fails with `path_outside_allowlist`**

- The path you're passing isn't under any `--allow-root`. Check the actual resolved paths: `python -c "from pathlib import Path; print(Path('/Users/you/data').resolve())"`
- If you're on macOS and use `/tmp`, that's actually `/private/tmp` after resolve — pass `--allow-root /private/tmp`.

**Pipeline takes longer than expected**

- Tier `full` on a large dataset can take 5+ minutes. Use `standard` or `quick` for quicker turnaround.
- Watch the MCP log to see when the call actually returns; Claude Desktop has a generous tool-call timeout but may still time out on multi-minute calls.

## Beyond Claude Desktop

The same MCP server works with:

- **Claude Code** — `~/.claude/settings.json` accepts the same `mcpServers` shape
- **Cursor** — see Cursor's MCP docs; same `command` / `args` shape
- **Custom agents using the MCP Python SDK** — `import mcp.client` and connect to `python -m aurora_mcp.server`

The Aurora MCP layer is transport-agnostic at the tool level. Stdio is the easiest; HTTP transport is on the v1.2 roadmap for remote-agent scenarios.

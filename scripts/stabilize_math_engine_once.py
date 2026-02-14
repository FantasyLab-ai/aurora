from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import py_compile

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
DEEP   = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def ensure_deep_math_exports(txt: str) -> str:
    # We want stable public symbols no matter how many times you refactor internals:
    # - compute_deep_math_v3 (preferred)
    # - compute_deep_math_v2 (compat)
    # - compute_deep_math (canonical alias for runner)
    #
    # Do NOT assume either exists; create wrappers if missing.

    # make sure basic imports exist (some earlier crashes were missing 're')
    if re.search(r"(?m)^\s*import\s+re\s*$", txt) is None:
        # insert after first import block
        lines = txt.splitlines()
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
            else:
                if i > 0:
                    break
        lines.insert(insert_at, "import re")
        txt = "\n".join(lines) + "\n"

    has_v3 = re.search(r"(?m)^\s*def\s+compute_deep_math_v3\s*\(", txt) is not None
    has_v2 = re.search(r"(?m)^\s*def\s+compute_deep_math_v2\s*\(", txt) is not None
    has_alias = re.search(r"(?m)^\s*compute_deep_math\s*=\s*compute_deep_math_v", txt) is not None

    tail = []

    # If v3 missing but v2 exists, alias v3 -> v2
    if not has_v3 and has_v2:
        tail.append("")
        tail.append("def compute_deep_math_v3(*args, **kwargs):")
        tail.append("    \"\"\"Compatibility wrapper: v3 routes to v2.\"\"\"")
        tail.append("    return compute_deep_math_v2(*args, **kwargs)")

    # If v2 missing but v3 exists, alias v2 -> v3
    if not has_v2 and (has_v3 or (not has_v3 and has_v2 is False)):
        # if neither exists, we can't invent the engine; but your file compiles,
        # so one of them is almost certainly there. We'll still handle the edge:
        tail.append("")
        tail.append("def compute_deep_math_v2(*args, **kwargs):")
        tail.append("    \"\"\"Compatibility wrapper: v2 routes to v3 when available.\"\"\"")
        tail.append("    fn = globals().get('compute_deep_math_v3')")
        tail.append("    if callable(fn):")
        tail.append("        return fn(*args, **kwargs)")
        tail.append("    raise NameError('compute_deep_math_v3 is not defined')")

    # Ensure canonical alias exists
    if not has_alias:
        tail.append("")
        tail.append("# Canonical alias used by the runner")
        tail.append("compute_deep_math = globals().get('compute_deep_math_v3') or globals().get('compute_deep_math_v2')")

    if tail:
        if not txt.endswith("\n"):
            txt += "\n"
        txt += "\n".join(tail) + "\n"

    return txt

def patch_runner_import_block(txt: str) -> str:
    # Remove any existing deep_math imports to avoid conflicts
    txt = re.sub(r"(?m)^\s*from\s+fantasyai\.aurora\.math\.deep_math\s+import\s+.*\n+", "", txt)

    import_block = (
        "# --- Deep Math (stable import) ---\n"
        "try:\n"
        "    from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as compute_deep_math\n"
        "except Exception:\n"
        "    from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as compute_deep_math\n"
    )

    lines = txt.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
        else:
            if i > 0:
                break
    lines.insert(insert_at, import_block.rstrip("\n"))
    return "\n".join(lines) + "\n"

def ensure_runner_prints_summary(txt: str) -> str:
    # If the runner used to print a final JSON summary and now doesn't, it feels like "nothing happened".
    # We'll ensure there is at least ONE unconditional print(json.dumps(summary, indent=2)) near the end of main().
    #
    # We patch gently: if we already see "dataset_key" and "run_dir" being printed via json.dumps, do nothing.
    if "json.dumps({" in txt and "dataset_key" in txt and "run_dir" in txt:
        return txt

    # Try to find where run_dir is set and add a final print block nearby.
    # We look for a variable named run_dir or RUN_DIR usage and append after the final write actions.
    # Fallback: append at end of main() before return.

    # ensure json import exists
    if re.search(r"(?m)^\s*import\s+json\s*$", txt) is None:
        txt = re.sub(r"(?m)^(import\s+argparse\s*\n)", r"\1import json\n", txt, count=1)

    # Insert a conservative print at end of file inside main() is hard by regex without breaking indentation.
    # We'll append a helper and call it from the bottom of main() if main exists.
    if "def _print_run_summary(" not in txt:
        txt += (
            "\n\ndef _print_run_summary(run_dir, dataset_key, table_path, seed, llm_used):\n"
            "    import json\n"
            "    summary = {\n"
            "        'dataset_key': dataset_key,\n"
            "        'table': str(table_path),\n"
            "        'seed': seed,\n"
            "        'run_dir': str(run_dir),\n"
            "        'llm_used': bool(llm_used),\n"
            "    }\n"
            "    print(json.dumps(summary, indent=2))\n"
        )

    # Now ensure main() calls _print_run_summary at least once.
    # We look for a line that writes storycard.json or deep_math.json and add call after that block.
    # If not found, we add call right before the last line of main() (heuristic: before \"if __name__ == '__main__'\").
    if "_print_run_summary(" in txt:
        # already being used somewhere
        return txt

    anchor = None
    m = re.search(r"(?m)^\s*#?\s*if\s+__name__\s*==\s*[\"']__main__[\"']\s*:\s*$", txt)
    if m:
        anchor = m.start()

    body = txt if anchor is None else txt[:anchor]
    tail = "" if anchor is None else txt[anchor:]

    # Find last occurrence of writing any json in run dir and inject call after it.
    # We'll patch after a line containing 'write_json' OR 'deep_math.json' OR 'storycard.json'
    inject_after_patterns = [
        r"(?m)^\s*.*deep_math\.json.*$",
        r"(?m)^\s*.*storycard\.json.*$",
        r"(?m)^\s*.*math_results\.json.*$",
        r"(?m)^\s*.*tableselection\.json.*$",
    ]

    inserted = False
    for pat in inject_after_patterns:
        matches = list(re.finditer(pat, body))
        if matches:
            last = matches[-1]
            # Determine indentation from that line
            line_start = body.rfind("\n", 0, last.start()) + 1
            line = body[line_start: body.find("\n", line_start)]
            indent = re.match(r"^\s*", line).group(0)

            injection = (
                f"\n{indent}# --- Always print a final run summary (UX) ---\n"
                f"{indent}try:\n"
                f"{indent}    _print_run_summary(run_dir, dataset_key, table_path, seed, llm_used)\n"
                f"{indent}except Exception:\n"
                f"{indent}    pass\n"
            )
            body = body[:last.end()] + injection + body[last.end():]
            inserted = True
            break

    if not inserted:
        # fallback: append at end of body (won't break compile)
        body += (
            "\n    # --- Always print a final run summary (UX) ---\n"
            "    try:\n"
            "        _print_run_summary(run_dir, dataset_key, table_path, seed, llm_used)\n"
            "    except Exception:\n"
            "        pass\n"
        )

    return body + tail

def main():
    if not RUNNER.exists():
        raise SystemExit(f"Missing: {RUNNER}")
    if not DEEP.exists():
        raise SystemExit(f"Missing: {DEEP}")

    b1 = backup(RUNNER); print(f"[OK] backup -> {b1}")
    b2 = backup(DEEP);   print(f"[OK] backup -> {b2}")

    deep_txt = DEEP.read_text(encoding="utf-8", errors="replace")
    deep_txt2 = ensure_deep_math_exports(deep_txt)
    DEEP.write_text(deep_txt2, encoding="utf-8")
    print("[OK] deep_math exports stabilized (v2/v3/alias + imports)")

    runner_txt = RUNNER.read_text(encoding="utf-8", errors="replace")
    runner_txt2 = patch_runner_import_block(runner_txt)
    runner_txt3 = ensure_runner_prints_summary(runner_txt2)
    RUNNER.write_text(runner_txt3, encoding="utf-8")
    print("[OK] runner stabilized (safe import + final JSON print)")

    py_compile.compile(str(DEEP), doraise=True)
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] stabilize patch applied + py_compile clean")

if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
DEEP_MATH = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def ensure_deep_math_v3():
    if not DEEP_MATH.exists():
        raise SystemExit(f"Missing: {DEEP_MATH}")

    backup(DEEP_MATH)
    txt = DEEP_MATH.read_text(encoding="utf-8")

    # Ensure compute_deep_math_v2 exists (we don't rewrite your engine here)
    if "def compute_deep_math_v2" not in txt:
        raise SystemExit("deep_math.py is missing compute_deep_math_v2; refusing to patch blindly.")

    # Add/replace a stable v3 wrapper + alias at EOF (idempotent).
    marker = "# === WORLDCLASS_STABLE_EXPORTS ==="
    block = f"""
{marker}
# Stable public entrypoints for the runner/API. v3 is allowed to call into v2 core,
# but must *label* outputs as v3 to prevent confusing downstream consumers.

def compute_deep_math_v3(df, seed: int = 0, **kwargs):
    \"\"\"Stable deep-math entrypoint used by the dataset runner.\"\"\"
    out = compute_deep_math_v2(df, seed=seed, **kwargs)
    if isinstance(out, dict):
        base_ver = out.get("version")
        out["base_version"] = base_ver
        out["version"] = "deep_math_v3"
    return out

# Canonical alias for callers that just want "compute_deep_math"
compute_deep_math = compute_deep_math_v3

__all__ = [
    "compute_deep_math_v2",
    "compute_deep_math_v3",
    "compute_deep_math",
]
""".lstrip("\n")

    if marker in txt:
        # Replace everything from marker to EOF
        head = txt.split(marker, 1)[0].rstrip() + "\n"
        txt2 = head + block
    else:
        txt2 = txt.rstrip() + "\n\n" + block

    DEEP_MATH.write_text(txt2, encoding="utf-8")
    print(f"[OK] deep_math stabilized: {DEEP_MATH}")

def patch_runner():
    if not RUNNER.exists():
        raise SystemExit(f"Missing: {RUNNER}")

    backup(RUNNER)
    txt = RUNNER.read_text(encoding="utf-8")

    # 1) Ensure we import a single canonical symbol (compute_deep_math) and keep fallback.
    import_block = """
# --- Deep Math (stable import) ---
try:
    from fantasyai.aurora.math.deep_math import compute_deep_math_v3 as _compute_deep_math
    _deep_math_version = "deep_math_v3"
except Exception:
    from fantasyai.aurora.math.deep_math import compute_deep_math_v2 as _compute_deep_math
    _deep_math_version = "deep_math_v2"

compute_deep_math = _compute_deep_math
""".strip()

    # Remove any previous deep_math import blocks/lines (broad but safe).
    # We avoid regex here to prevent escaping errors.
    lines = txt.splitlines()
    filtered = []
    skip = False
    for line in lines:
        if line.strip().startswith("# --- Deep Math (stable import) ---"):
            skip = True
        if not skip:
            if "from fantasyai.aurora.math.deep_math import" in line:
                continue
            filtered.append(line)
        if skip and line.strip() == "" and (len(filtered) > 0):
            # allow block to end on blank line; but some variants won't have a clear end.
            # We'll stop skipping after we see a blank line AND we already started skipping.
            # (This is intentionally conservative.)
            skip = False

    txt2 = "\n".join(filtered).strip() + "\n"

    # Insert import block after the initial import section.
    lines2 = txt2.splitlines()
    insert_at = 0
    for i, line in enumerate(lines2):
        if line.startswith("import ") or line.startswith("from "):
            insert_at = i + 1
        else:
            if i > 0:
                break
    lines2.insert(insert_at, import_block)
    txt3 = "\n".join(lines2) + "\n"

    # 2) Ensure deep-math execution + deep_math.json write exists right after math_results.json write.
    anchor = '(run_dir / "math_results.json").write_text(json.dumps(math_results, indent=2), encoding="utf-8")'
    if anchor not in txt3:
        # Some versions split this across multiple lines; fallback anchor:
        if 'math_results.json' not in txt3:
            raise SystemExit("Could not locate math_results.json write to anchor deep-math insertion.")
        # We'll just append near end of also-math block later.
        anchor = None

    deep_block = """
        # --- Deep Math execution (writes deep_math.json) ---
        deep_math_enabled = bool(getattr(args, "deep_math", False))
        if deep_math_enabled:
            try:
                deep_out = compute_deep_math(df, seed=int(args.seed))
                if isinstance(deep_out, dict):
                    deep_out.setdefault("deep_math_version", _deep_math_version)
            except Exception as e:
                deep_out = {"error": f"deep_math_failed:{type(e).__name__}:{e}", "deep_math_version": _deep_math_version}

            (run_dir / "deep_math.json").write_text(json.dumps(deep_out, indent=2), encoding="utf-8")
""".rstrip("\n")

    if "deep_math.json" not in txt3:
        if anchor:
            txt3 = txt3.replace(anchor, anchor + "\n" + deep_block)
        else:
            # Fallback: append deep block after math_results write in a more general way.
            idx = txt3.find("math_results.json")
            if idx == -1:
                raise SystemExit("Could not insert deep-math block (no anchor found).")
            # Insert after the next newline following that line.
            pre = txt3[:idx]
            post = txt3[idx:]
            line_end = post.find("\n")
            txt3 = pre + post[:line_end+1] + deep_block + "\n" + post[line_end+1:]

    # 3) Ensure final summary print exists (runner got “silent”).
    # We will append a print at the end of main() if not present.
    if '"llm_used": true' in txt3 or "llm_used" in txt3:
        # already prints somewhere; don't force
        pass

    # Simple heuristic: ensure at least one print(json.dumps({...})) exists.
    if "print(json.dumps" not in txt3:
        # Add near end of main() before function returns by appending at EOF a safe print
        # by inserting just before the last line if it's "if __name__ == '__main__':"
        lines3 = txt3.splitlines()
        tail_idx = len(lines3)
        for i, line in enumerate(lines3):
            if line.strip().startswith("if __name__"):
                tail_idx = i
                break
        summary = """
    # --- final run summary (stdout) ---
    summary = {
        "dataset_key": ds_key,
        "folder": str(folder),
        "table": str(table),
        "seed": int(args.seed),
        "run_dir": str(run_dir),
        "math_stack": str(run_dir),
        "llm_used": bool(getattr(args, "also_llm", False)),
        "deep_math_enabled": bool(getattr(args, "deep_math", False)),
    }
    print(json.dumps(summary, indent=2))
""".rstrip("\n")
        # Try to inject before tail_idx with correct indentation at function scope
        lines3.insert(tail_idx, summary)
        txt3 = "\n".join(lines3) + "\n"

    RUNNER.write_text(txt3, encoding="utf-8")
    print(f"[OK] runner stabilized: {RUNNER}")

def main():
    ensure_deep_math_v3()
    patch_runner()

    import py_compile
    py_compile.compile(str(DEEP_MATH), doraise=True)
    py_compile.compile(str(RUNNER), doraise=True)
    print("[DONE] Deep-math + runner stabilized and compile clean.")

if __name__ == "__main__":
    main()

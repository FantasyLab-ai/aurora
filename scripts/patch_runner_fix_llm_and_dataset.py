from __future__ import annotations

import argparse
import re
from pathlib import Path

def die(msg: str, code: int = 1) -> None:
    raise SystemExit(msg)

def ensure_strip_think(src: str) -> str:
    """
    Ensure _strip_think(text) exists as a top-level utility function (not nested).
    If already present, we leave it alone.
    """
    if re.search(r"(?m)^def _strip_think\s*\(", src):
        return src

    insert_after = re.search(r"(?s)(def _safe_str\(.+?\n)(\n)", src)
    if not insert_after:
        # Fall back: insert after _write_json
        insert_after = re.search(r"(?s)(def _write_json\(.+?\n)(\n)", src)
    if not insert_after:
        die("ERROR: Could not find a safe place to insert _strip_think (missing _safe_str/_write_json).")

    block = r'''
def _strip_think(text: str) -> str:
    """
    Remove DeepSeek-style <think>...</think> blocks and trim.
    Keeps the user-facing answer only.
    """
    import re
    if not text:
        return ""
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", text)
    return cleaned.strip()
'''.lstrip("\n")

    i = insert_after.end(1)
    return src[:i] + "\n" + block + src[i:]

def fix_resolve_dataset_table(src: str) -> str:
    """
    Replace the entire _resolve_dataset_table function with a known-good implementation.
    This also removes any accidental nested insertions (like _strip_think inside it).
    """
    pattern = re.compile(
        r"(?s)^def _resolve_dataset_table\(dataset_key: str\) -> Dict\[str, str\]:.*?^def _load_df\(",
        re.M
    )
    m = pattern.search(src)
    if not m:
        die("ERROR: Could not locate _resolve_dataset_table(...) block to replace (anchor: def _load_df).")

    replacement = r'''
def _resolve_dataset_table(dataset_key: str) -> Dict[str, str]:
    """
    This is intentionally conservative: it reuses your existing external folder structure.
    It expects: fantasyai/data/external/<dataset_key>/raw/<one csv>
    """
    root = Path(__file__).resolve().parents[1]
    folder = root / "fantasyai" / "data" / "external" / dataset_key / "raw"
    if not folder.exists():
        raise FileNotFoundError(f"Missing dataset folder: {folder}")

    csvs = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not csvs:
        raise FileNotFoundError(f"No .csv found in: {folder}")

    table = csvs[0]
    return {"folder": str(folder), "table": str(table)}

def _load_df'''.lstrip("\n")

    return src[:m.start()] + replacement + src[m.end():]

def fix_llm_block(src: str) -> str:
    """
    Replace the entire 'LLM narrative (optional)' section with a clean, properly-indented version.
    """
    pattern = re.compile(
        r"(?s)^(\s*)# LLM narrative \(optional\)\s*\n\1if args\.also_llm:.*?^\1# FINAL stdout summary",
        re.M
    )
    m = pattern.search(src)
    if not m:
        die("ERROR: Could not locate the LLM narrative section to replace (anchor: '# FINAL stdout summary').")

    indent = m.group(1)  # keep whatever indentation level the file uses (should be 4 spaces)
    block = f"""{indent}# LLM narrative (optional)
{indent}if args.also_llm:
{indent}    provider = os.environ.get("AURORA_LLM_PROVIDER", "").strip().lower()
{indent}    debug: Dict[str, Any] = {{"provider": provider}}
{indent}    narrative: Dict[str, Any] = {{"version": "llm_narrative_v1", "provider": provider, "text": ""}}

{indent}    try:
{indent}        if provider == "ollama":
{indent}            prompt = (
{indent}                "You are Aurora, a quantitative analyst. "
{indent}                "Write a concise research note based on these JSON snippets.\\n\\n"
{indent}                f"math_results:\\n{{json.dumps(math_results, indent=2)[:6000]}}\\n\\n"
{indent}                f"deep_math:\\n{{json.dumps(deep_out, indent=2)[:6000]}}\\n\\n"
{indent}                "Return: (1) 5 bullet insights, (2) 3 risks/assumptions, (3) 3 next analyses."
{indent}            )
{indent}            resp = _ollama_generate(prompt)
{indent}            llm_used = True

{indent}            # Extract + sanitize model output (DeepSeek often includes <think>...</think>)
{indent}            raw_text = resp.get("response", "") or resp.get("message", {{}}).get("content", "")
{indent}            clean_text = _strip_think(raw_text)

{indent}            # Save only the clean narrative (avoid dumping hidden reasoning)
{indent}            narrative["text"] = clean_text

{indent}            # Keep debug metadata only (raw payload only if explicitly enabled)
{indent}            debug["ok"] = True
{indent}            debug["model"] = os.environ.get("OLLAMA_MODEL", "")
{indent}            debug["url"] = os.environ.get("OLLAMA_URL", "")
{indent}            debug["generated_at"] = datetime.now().isoformat(timespec="seconds")

{indent}            if os.environ.get("AURORA_LLM_DEBUG_RAW", "").strip() == "1":
{indent}                debug["raw"] = resp
{indent}        else:
{indent}            narrative["text"] = (
{indent}                "LLM provider not configured. Set AURORA_LLM_PROVIDER=ollama and OLLAMA_URL/OLLAMA_MODEL."
{indent}            )
{indent}    except Exception as e:
{indent}        debug["error"] = f"{{type(e).__name__}}:{{e}}"
{indent}        debug["trace"] = traceback.format_exc()[:8000]
{indent}        narrative["text"] = (
{indent}            "LLM call failed; see llm_debug.json. Core math outputs are still valid."
{indent}        )

{indent}    _write_json(run_dir / "llm_debug.json", debug)
{indent}    _write_json(run_dir / "llm_narrative.json", narrative)

{indent}# FINAL stdout summary
"""
    return src[:m.start()] + block + src[m.end():]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        die(f"ERROR: file not found: {p}")

    src = p.read_text(encoding="utf-8", errors="replace")

    # 1) Ensure _strip_think exists (top-level)
    src = ensure_strip_think(src)

    # 2) Fix dataset resolver function
    src = fix_resolve_dataset_table(src)

    # 3) Fix LLM narrative block (indent-safe)
    src = fix_llm_block(src)

    p.write_text(src, encoding="utf-8")
    print(f"[OK] patched -> {p}")

if __name__ == "__main__":
    main()

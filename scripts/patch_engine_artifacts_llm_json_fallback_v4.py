from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
import py_compile

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "scripts" / "engine_artifacts.py"

    if not target.exists():
        print(f"[ERROR] not found: {target}")
        return 2

    original = target.read_text(encoding="utf-8-sig")
    text = original

    # --- 0) Safety backup ---
    bak = target.with_suffix(f".py.bak_llmfix")
    if not bak.exists():
        bak.write_text(original, encoding="utf-8")
        print(f"[OK] backup -> {bak}")

    # --- 1) Ensure we have a robust JSON extraction helper inside the file ---
    # We'll inject helper functions near the top (after imports) if they don't exist.
    if "_extract_first_json_object" not in text:
        helper = r'''
def _extract_first_json_object(s: str):
    """
    Robustly extracts the first valid JSON object found in a string.
    Uses JSONDecoder.raw_decode starting from each '{' occurrence.
    Returns dict if found, else None.
    """
    try:
        dec = json.JSONDecoder()
    except Exception:
        return None

    if not s:
        return None

    # Fast path: try whole string
    ss = s.strip()
    if ss.startswith("{") and ss.endswith("}"):
        try:
            obj = json.loads(ss)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # Scan for first decodable object
    for m in re.finditer(r"\{", s):
        i = m.start()
        try:
            obj, end = dec.raw_decode(s[i:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None
'''
        # Insert after the last optional-deps import block, or after requests import, or after imports.
        anchor_patterns = [
            "try:\n    import requests",
            "except Exception:  # pragma: no cover\n    requests = None  # type: ignore",
        ]
        anchor_idx = -1
        for ap in anchor_patterns:
            anchor_idx = text.find(ap)
            if anchor_idx != -1:
                break

        if anchor_idx != -1:
            # place helper after the requests optional import block (best spot)
            # find end of that block by finding the next blank line after it
            end_block = text.find("\n\n", anchor_idx)
            if end_block == -1:
                end_block = anchor_idx
            insert_at = end_block + 2
            text = text[:insert_at] + helper + "\n" + text[insert_at:]
            print("[OK] injected _extract_first_json_object helper")
        else:
            # fallback: prepend after imports
            text = helper + "\n" + text
            print("[OK] injected helper at top (fallback)")

    # --- 2) Fix the NameError bug: deep_math -> deep ---
    # These calls exist in generate_guarded_llm_summary in your file.
    before = text
    text = text.replace("_fallback_llm_summary(scorecard, deep_math)", "_fallback_llm_summary(scorecard, deep)")
    if text != before:
        print("[OK] fixed fallback NameError (deep_math -> deep)")
    else:
        print("[WARN] fallback NameError pattern not found (may already be fixed)")

    # --- 3) Replace brittle brace slicing JSON parse with robust extractor ---
    # We patch the block:
    #   s = txt; a=s.find('{'); b=s.rfind('}'); json.loads(s[a:b+1])
    # into:
    #   obj = _extract_first_json_object(txt)
    #   if obj: out['parsed']=obj else fallback...
    #
    # We'll do a conservative regex replacement to avoid breaking formatting.
    parse_block_re = re.compile(
        r"""
        \s*s\s*=\s*txt\s*\n
        \s*a\s*=\s*s\.find\(\s*"\{"\s*\)\s*\n
        \s*b\s*=\s*s\.rfind\(\s*"\}"\s*\)\s*\n
        \s*if\s+a\s*!=\s*-1\s+and\s+b\s*!=\s*-1\s+and\s+b\s*>\s*a\s*:\s*\n
        \s*obj\s*=\s*json\.loads\(\s*s\s*\[\s*a\s*:\s*b\s*\+\s*1\s*\]\s*\)\s*\n
        \s*out\["parsed"\]\s*=\s*obj\s*\n
        \s*else\s*:\s*\n
        \s*out\["parsed_error"\]\s*=\s*"no_json_object_detected"\s*\n
        \s*out\["parsed"\]\s*=\s*_fallback_llm_summary\(scorecard,\s*deep(?:_math)?\)\s*\n
        \s*out\["note"\]\s*=\s*"fallback_used"\s*
        """,
        re.VERBOSE,
    )

    replacement = """
            obj = _extract_first_json_object(txt)
            if obj is not None:
                out["parsed"] = obj
            else:
                out["parsed_error"] = "no_json_object_detected"
                out["parsed"] = _fallback_llm_summary(scorecard, deep)
                out["note"] = "fallback_used"
""".rstrip()

    if parse_block_re.search(text):
        text = parse_block_re.sub("\n" + replacement + "\n", text, count=1)
        print("[OK] replaced brittle JSON parsing with robust extractor")
    else:
        print("[WARN] brittle JSON parsing block not found (may already be changed); no replacement made")

    # --- 4) Ensure 're' is imported (helper uses it) ---
    # Your file already imports re in this patch script, but engine_artifacts.py might not.
    if "import re" not in text:
        # Insert after 'import json'
        text = text.replace("import json\n", "import json\nimport re\n", 1)
        print("[OK] injected import re into engine_artifacts.py")

    # --- 5) Write + compile ---
    if text == original:
        print("[INFO] No changes made.")
        return 0

    target.write_text(text, encoding="utf-8")
    print(f"[OK] wrote -> {target} (sha={sha256_text(text)})")

    py_compile.compile(str(target), doraise=True)
    print("[DONE] engine_artifacts.py compiled clean")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

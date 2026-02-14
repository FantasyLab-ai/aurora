from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
import py_compile


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def write_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8", newline="\n")


def remove_bom(s: str) -> str:
    # Strip UTF-8 BOM if present
    return s.lstrip("\ufeff")


def extract_first_json_object(text: str) -> str | None:
    """
    Robustly extract the first top-level JSON object {...} from a messy LLM response.
    Handles braces inside strings and escape sequences.
    Returns the JSON substring or None.
    """
    if not text:
        return None

    start = text.find("{")
    if start < 0:
        return None

    in_str = False
    esc = False
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        else:
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

    return None


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "scripts" / "engine_artifacts.py"
    if not target.exists():
        raise SystemExit(f"ERROR: not found: {target}")

    original = remove_bom(target.read_text(encoding="utf-8"))
    backup = target.with_suffix(target.suffix + f".bak_llmfixv5_{time.strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(original, encoding="utf-8")
    print(f"[OK] backup -> {backup}")

    s = original

    # 1) Ensure helper exists at module level (insert near top, after imports)
    if "def _extract_first_json_object(" not in s:
        helper_block = r'''
def _extract_first_json_object(text: str) -> dict | None:
    """
    Extract the first JSON object from a messy LLM response.
    Returns parsed dict or None.
    """
    sub = None
    try:
        sub = extract_first_json_object(text)
        if not sub:
            return None
        obj = json.loads(sub)
        if isinstance(obj, dict):
            return obj
        return None
    except Exception:
        return None
'''.lstrip("\n")

        # Insert after the last import block (safe heuristic)
        m = re.search(r"(\n)(def\s+)", s)
        if not m:
            raise SystemExit("ERROR: could not find insertion point for helper")
        ins = m.start(2)
        s = s[:ins] + helper_block + "\n\n" + s[ins:]
        print("[OK] injected _extract_first_json_object helper")

    # 2) Fix NameError: deep_math -> deep inside generate_guarded_llm_summary fallback
    s2 = s.replace("_fallback_llm_summary(scorecard, deep_math)", "_fallback_llm_summary(scorecard, deep)")
    if s2 != s:
        s = s2
        print("[OK] fixed fallback NameError (deep_math -> deep)")

    # 3) Replace brittle JSON parsing region inside generate_guarded_llm_summary
    # We target the try block that does: obj = json.loads(s[a:b+1]) or json.loads(txt)
    # and replace only that internal logic with robust extractor + fallback.
    def_pat = r"def\s+generate_guarded_llm_summary\([^\)]*\):"
    dm = re.search(def_pat, s)
    if not dm:
        raise SystemExit("ERROR: generate_guarded_llm_summary not found")

    # Find the first occurrence of 'json.loads(' AFTER the function definition and patch around it.
    func_start = dm.start()
    func_end = s.find("\ndef ", func_start + 1)
    if func_end == -1:
        func_end = len(s)
    func = s[func_start:func_end]

    # We patch by replacing the block that currently tries json.loads(...) with:
    #   parsed = _extract_first_json_object(txt)
    #   if parsed: out["parsed"]=parsed else fallback
    #   (in the existing try/except)
    if "json.loads" not in func:
        print("[WARN] no json.loads found in generate_guarded_llm_summary; leaving parsing as-is")
    else:
        # Find the try: that contains json.loads
        # We'll replace from the line containing 'obj = json.loads' up to the line setting out["parsed"] = obj
        lines = func.splitlines(True)

        # locate indices
        i_load = None
        for i, ln in enumerate(lines):
            if "json.loads" in ln:
                i_load = i
                break

        if i_load is None:
            print("[WARN] could not locate json.loads line; leaving parsing as-is")
        else:
            # find indentation of that block
            indent = re.match(r"^(\s*)", lines[i_load]).group(1)

            # walk backward to find the start of the try block (line that starts with indent + "try:")
            i_try = i_load
            while i_try >= 0 and not re.match(rf"^{re.escape(indent)}try\s*:", lines[i_try]):
                i_try -= 1

            if i_try < 0:
                print("[WARN] could not find matching try: for json parsing; leaving parsing as-is")
            else:
                # find the except that matches this try (same indent level)
                i_except = i_try + 1
                while i_except < len(lines) and not re.match(rf"^{re.escape(indent)}except\s+Exception", lines[i_except]):
                    i_except += 1

                if i_except >= len(lines):
                    print("[WARN] could not find except for json parsing try; leaving parsing as-is")
                else:
                    # Replace only the body of try (from i_try+1 to i_except-1)
                    new_try_body = (
                        f"{indent}    parsed = _extract_first_json_object(txt)\n"
                        f"{indent}    if parsed is not None:\n"
                        f"{indent}        out[\"parsed\"] = parsed\n"
                        f"{indent}        out[\"parsed_error\"] = None\n"
                        f"{indent}        out[\"note\"] = \"parsed_ok\"\n"
                        f"{indent}    else:\n"
                        f"{indent}        out[\"parsed\"] = _fallback_llm_summary(scorecard, deep)\n"
                        f"{indent}        out[\"parsed_error\"] = \"no_json_object_detected\"\n"
                        f"{indent}        out[\"note\"] = \"fallback_used_nojson\"\n"
                    )

                    # Keep the except block, but make it consistent and never throw
                    # Ensure except body has a fallback too
                    # We will overwrite the first few lines of except body if necessary.
                    # Find end of except block (next line with indent and not deeper OR function end)
                    # We'll just rewrite the first lines after except to ensure correctness.
                    # Determine except indent (same as indent)
                    # Determine body indent (indent + 4 spaces)
                    body_indent = indent + "    "

                    # Replace try body
                    lines[i_try + 1 : i_except] = [new_try_body]

                    # Ensure except has at least one line of fallback, and no broken extra indentation.
                    # Replace everything between 'except Exception as e:' and the next non-indented-at-body line
                    j = i_except + 1
                    while j < len(lines) and (lines[j].startswith(body_indent) or lines[j].strip() == ""):
                        j += 1

                    new_except_body = (
                        f"{body_indent}out[\"parsed\"] = _fallback_llm_summary(scorecard, deep)\n"
                        f"{body_indent}out[\"parsed_error\"] = str(e)\n"
                        f"{body_indent}out[\"note\"] = \"fallback_used_exception\"\n"
                    )

                    lines[i_except + 1 : j] = [new_except_body]

                    func_new = "".join(lines)
                    s = s[:func_start] + func_new + s[func_end:]
                    print("[OK] replaced brittle JSON parsing with robust extractor + safe fallback")

    # 4) Make sure generate_guarded_llm_summary never crashes the pipeline
    # If anything unexpected happens, it should write a minimal guarded file and return.
    # We'll add an outer try/except only if not already present.
    if "NOTE: outer_guard" not in s:
        # Very light-touch: wrap the whole function body after signature with try/except.
        # Only do this if it doesn't already have an outer guard.
        # Heuristic: insert after function docstring or first line.
        pass  # keeping it minimal since we already stabilized the inner parse

    # Write + compile
    write_text(target, s)
    print(f"[OK] wrote -> {target} (sha={sha256(target)})")

    py_compile.compile(str(target), doraise=True)
    print("[DONE] engine_artifacts.py compiled clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

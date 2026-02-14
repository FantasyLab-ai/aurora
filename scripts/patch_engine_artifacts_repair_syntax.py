import re
import sys
from pathlib import Path
import py_compile

TARGET = Path("scripts/engine_artifacts.py")

def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}")
    raise SystemExit(code)

def main():
    if not TARGET.exists():
        die(f"not found: {TARGET}")

    text = TARGET.read_text(encoding="utf-8", errors="replace")

    # ---- 1) Ensure __future__ import is first (remove anything before it) ----
    fut = "from __future__ import annotations"
    if fut in text:
        # Drop everything before the first future import line
        idx = text.find(fut)
        text = text[idx:]
    else:
        # Prepend it if missing
        text = fut + "\n\n" + text

    # Remove any physics imports that were injected above/around future imports
    # (we will re-add physics cleanly later once df/run context is wired properly)
    text = re.sub(r"^\s*from\s+fantasyai\.aurora\.physics\.[^\n]+\n", "", text, flags=re.M)

    # ---- 2) Remove the BAD physics injection that landed inside scorecard dict ----
    # We remove everything between `scorecard = {` and the first `"engine_version":` entry,
    # then keep `"engine_version": ...` onward intact.
    marker_a = "scorecard = {"
    marker_b = '"engine_version"'
    a = text.find(marker_a)
    b = text.find(marker_b, a if a != -1 else 0)

    if a != -1 and b != -1 and b > a:
        # keep up to 'scorecard = {' plus newline, then keep from the engine_version line
        head = text[: a + len(marker_a)]
        tail = text[b:]
        # ensure formatting: put a newline after the opening brace
        if not head.endswith("\n"):
            head += "\n"
        # remove any dangling indentation before engine_version
        tail = re.sub(r"^[ \t]*", "        ", tail, flags=re.M, count=1) if tail.lstrip().startswith('"engine_version"') else tail
        text = head + "\n" + tail
    else:
        # If we can't find markers, don't guess destructively
        die("Could not locate scorecard dict markers to repair. Restore from backup and re-run.")

    # Also remove any leftover references to write_physics_artifacts / write_physics_bundle
    text = re.sub(r"write_physics_artifacts", "write_physics_bundle", text)
    # If any calls remain, delete those blocks safely (we don't have df available here)
    text = re.sub(
        r"\n[ \t]*# --- Physics.*?--- end physics.*?\n",
        "\n",
        text,
        flags=re.S
    )

    # ---- 3) Remove the earlier duplicate fallback summary (the one with (scorecard: dict, deep: dict)) ----
    # Keep the later UI-oriented _fallback_llm_summary(scorecard: Dict[str, Any], deep_math: Dict[str, Any])
    pat = r"def _fallback_llm_summary\(scorecard:\s*dict,\s*deep:\s*dict\)\s*->\s*dict:\s*\n.*?\n\n(?=def _read_json\()"
    text2, n = re.subn(pat, "", text, flags=re.S)
    text = text2

    # ---- 4) Write and compile-check ----
    TARGET.write_text(text, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)
    print("[OK] Repaired engine_artifacts.py and it compiles clean.")

if __name__ == "__main__":
    main()

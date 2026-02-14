from __future__ import annotations

import re
import json
import shutil
from pathlib import Path
import py_compile
from datetime import datetime

def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def _extract_first_json_object(text: str):
    """
    Robustly extract the first valid JSON object from a messy LLM response.
    Handles leading/trailing text, markdown fences, <think> blocks, etc.
    Returns (obj, err) where obj is dict/list or None.
    """
    if not isinstance(text, str) or not text.strip():
        return None, "empty_text"

    s = text.strip()

    # Strip common markdown fences but keep contents
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)

    dec = json.JSONDecoder()

    # Scan for any '{' or '[' and attempt json decode from there
    candidates = []
    for m in re.finditer(r"[\{\[]", s):
        candidates.append(m.start())

    for i in candidates:
        try:
            obj, end = dec.raw_decode(s[i:])
            return obj, None
        except Exception:
            continue

    return None, "no_valid_json_found"

def main():
    repo_root = Path(__file__).resolve().parents[1]  # ...\Aurora_QIE
    target = repo_root / "scripts" / "engine_artifacts.py"
    if not target.exists():
        raise SystemExit(f"ERROR: not found: {target}")

    backup = target.with_name(f"engine_artifacts.py.bak_llmfixv6_{_now_tag()}")
    shutil.copy2(target, backup)
    print(f"[OK] backup -> {backup}")

    src = target.read_text(encoding="utf-8")

    # 1) Ensure helper exists (inject near other helpers). If already present, skip.
    if "_extract_first_json_object" not in src:
        # place after build_guardrailed_llm_prompt definition if present, else near top after imports
        anchor = "def build_guardrailed_llm_prompt"
        if anchor in src:
            insert_at = src.index(anchor)
            helper_block = (
                "\n\n"
                "def _extract_first_json_object(text: str):\n"
                "    \"\"\"Robustly extract the first valid JSON object/array from a messy LLM response.\"\"\"\n"
                "    if not isinstance(text, str) or not text.strip():\n"
                "        return None, \"empty_text\"\n"
                "    s = text.strip()\n"
                "    import re as _re\n"
                "    s = _re.sub(r\"^```(?:json)?\\s*\", \"\", s, flags=_re.IGNORECASE)\n"
                "    s = _re.sub(r\"\\s*```$\", \"\", s)\n"
                "    dec = json.JSONDecoder()\n"
                "    import re\n"
                "    for m in re.finditer(r\"[\\{\\[]\", s):\n"
                "        i = m.start()\n"
                "        try:\n"
                "            obj, _end = dec.raw_decode(s[i:])\n"
                "            return obj, None\n"
                "        except Exception:\n"
                "            continue\n"
                "    return None, \"no_valid_json_found\"\n"
            )
            src = src[:insert_at] + helper_block + src[insert_at:]
            print("[OK] injected _extract_first_json_object helper")
        else:
            print("[WARN] build_guardrailed_llm_prompt anchor not found; helper not injected (will patch function to include local extractor).")

    # 2) Replace ONLY the body of generate_guarded_llm_summary with a safe version.
    # We locate the function and replace it until the next top-level def.
    pattern = re.compile(
        r"^def\s+generate_guarded_llm_summary\s*\(.*?\):\n(?P<body>(?:^[ \t].*\n)*)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(src)
    if not m:
        raise SystemExit("ERROR: generate_guarded_llm_summary not found (restore engine_artifacts.py from a clean backup and retry).")

    new_func = (
        "def generate_guarded_llm_summary(run_dir: Path, scorecard: Dict[str, Any]) -> Dict[str, Any]:\n"
        "    \"\"\"\n"
        "    Writes llm_narrative_guarded.json. Never crashes the pipeline.\n"
        "    If the LLM returns non-JSON or invalid JSON, we fall back to a deterministic summary.\n"
        "    \"\"\"\n"
        "    rp = RunPaths.from_run_dir(run_dir)\n"
        "    deep = _read_json(rp.deep_math)\n"
        "\n"
        "    prompt = build_guardrailed_llm_prompt(scorecard, deep)\n"
        "    ok, txt = _ollama_generate(prompt, timeout=90)\n"
        "    out: Dict[str, Any] = {\n"
        "        \"ok\": bool(ok),\n"
        "        \"model\": os.getenv(\"OLLAMA_MODEL\"),\n"
        "        \"provider\": os.getenv(\"AURORA_LLM_PROVIDER\"),\n"
        "        \"generated_at\": _now_iso(),\n"
        "        \"raw_response\": txt,\n"
        "    }\n"
        "\n"
        "    # Always produce a parsed object (either LLM JSON or fallback)\n"
        "    if ok and isinstance(txt, str) and txt.strip():\n"
        "        try:\n"
        "            obj, err = _extract_first_json_object(txt) if '_extract_first_json_object' in globals() else (None, 'extractor_missing')\n"
        "            if obj is not None:\n"
        "                out[\"parsed\"] = obj\n"
        "            else:\n"
        "                out[\"parsed_error\"] = err or \"no_json_object_detected\"\n"
        "                out[\"parsed\"] = _fallback_llm_summary(scorecard, deep)\n"
        "                out[\"note\"] = \"fallback_used\"\n"
        "        except Exception as e:\n"
        "            out[\"parsed_error\"] = str(e)\n"
        "            out[\"parsed\"] = _fallback_llm_summary(scorecard, deep)\n"
        "            out[\"note\"] = \"fallback_used_exception\"\n"
        "    else:\n"
        "        out[\"parsed_error\"] = \"llm_unavailable_or_empty\"\n"
        "        out[\"parsed\"] = _fallback_llm_summary(scorecard, deep)\n"
        "        out[\"note\"] = \"fallback_used\"\n"
        "\n"
        "    _write_json(run_dir / \"llm_narrative_guarded.json\", out)\n"
        "    return out\n"
    )

    # splice: replace from function start to just after its indented block
    func_start = m.start()
    func_header_end = src.find("\n", func_start) + 1

    # Find the next top-level def after the function header
    # by scanning after header_end for "\ndef " at column 0
    rest = src[func_header_end:]
    next_def = re.search(r"^\ndef\s", rest, flags=re.MULTILINE)
    if next_def:
        func_end = func_header_end + next_def.start() + 1  # include the preceding newline
    else:
        func_end = len(src)

    src = src[:func_start] + new_func + src[func_end:]
    target.write_text(src, encoding="utf-8")
    print(f"[OK] wrote -> {target}")

    py_compile.compile(str(target), doraise=True)
    print("[DONE] engine_artifacts.py compiled clean")

if __name__ == "__main__":
    main()

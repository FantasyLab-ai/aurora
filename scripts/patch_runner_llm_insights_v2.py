from __future__ import annotations
import re
import sys
from pathlib import Path

def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)

def ensure_import_re(text: str) -> str:
    # If "import re" already exists anywhere, do nothing.
    if re.search(r"(?m)^\s*import\s+re\s*$", text):
        return text

    # Insert `import re` after other stdlib imports if possible.
    # We look for `import json` and insert after it; else after `import os`; else after `import argparse`.
    for anchor in [r"(?m)^import\s+json\s*$", r"(?m)^import\s+os\s*$", r"(?m)^import\s+argparse\s*$"]:
        m = re.search(anchor, text)
        if m:
            insert_at = m.end()
            return text[:insert_at] + "\nimport re" + text[insert_at:]

    # Fallback: put near top after future import line
    m = re.search(r"(?m)^from __future__ import annotations\s*$", text)
    if m:
        insert_at = m.end()
        return text[:insert_at] + "\nimport re" + text[insert_at:]

    # Last resort: prepend
    return "import re\n" + text

def patch_ollama_payload(text: str) -> str:
    # Replace the payload dict inside _ollama_generate with one that:
    # - forces JSON via "format": "json"
    # - adds options with temperature (env controlled)
    # We patch only inside def _ollama_generate(...): block.
    pattern = r"(?s)(def _ollama_generate\(prompt: str\) -> Dict\[str, Any\]:\s*.*?\n\s*payload\s*=\s*\{\s*\n)(.*?)(\n\s*\}\s*)"
    m = re.search(pattern, text)
    if not m:
        die("Could not locate payload dict inside _ollama_generate()")

    new_payload_body = """        "model": model,
        "prompt": prompt,
        "stream": False,

        # Force structured output where supported (Ollama server enforces JSON framing)
        "format": "json",

        # Make outputs tighter / less rambly
        "options": {
            "temperature": float(os.environ.get("AURORA_LLM_TEMP", "0.2")),
        },
"""
    return text[:m.start(2)] + new_payload_body + text[m.end(2):]

def patch_llm_prompt_and_parse(text: str) -> str:
    # Replace the prompt construction + response extraction/handling block.
    # We anchor on: `prompt = (` and the following `resp = _ollama_generate(prompt)`
    pattern = r"""(?sx)
        (?P<prefix>\n\s*if\s+provider\s*==\s*"ollama":\s*\n)
        (?P<body>
            \s*prompt\s*=\s*\(.*?\)\s*\n
            \s*resp\s*=\s*_ollama_generate\(prompt\)\s*\n
            \s*llm_used\s*=\s*True\s*\n
            .*?
            \s*narrative\["text"\]\s*=\s*clean_text\s*\n
            .*?
            (?:\n\s*if\s+os\.environ.*?\n\s*debug\["raw"\]\s*=\s*resp\s*)?
        )
    """
    m = re.search(pattern, text)
    if not m:
        die("Could not locate the ollama prompt/parse block to replace (anchor: if provider == \"ollama\")")

    replacement = r'''
            if provider == "ollama":
                # Demand STRICT JSON so we can reliably parse insights.
                prompt = (
                    "You are Aurora, a quantitative environmental analyst.\\n"
                    "You are given structured engine outputs in JSON (math_results + deep_math).\\n\\n"
                    "TASK: Produce an INSIGHT NOTE *only* about the provided data.\\n"
                    "Return ONLY a single valid JSON object (no markdown, no extra text).\\n"
                    "Do NOT include <think> tags or hidden reasoning.\\n\\n"
                    "JSON SCHEMA:\\n"
                    "{\\n"
                    '  "one_paragraph_summary": "string",\\n'
                    '  "insights": ["5-10 crisp bullets grounded in math_results/deep_math"],\\n'
                    '  "risks_assumptions": ["3-6 items"],\\n'
                    '  "next_analyses": ["3-8 items"],\\n'
                    '  "signal_calls": ["optional: 1-5 recommended actions / what to watch"]\\n'
                    "}\\n\\n"
                    "math_results:\\n"
                    + json.dumps(math_results, indent=2)[:9000]
                    + "\\n\\n"
                    "deep_math:\\n"
                    + json.dumps(deep_out, indent=2)[:9000]
                )

                resp = _ollama_generate(prompt)
                llm_used = True

                # Extract raw model output; Ollama generate returns {"response": "..."} commonly.
                raw_text = resp.get("response", "") or resp.get("message", {}).get("content", "")
                raw_text = _safe_str(raw_text, max_len=200000)

                # Strip any accidental hidden reasoning blocks
                clean_text = _strip_think(raw_text)

                # Try hard to parse JSON
                parsed = None
                parse_error = None
                try:
                    parsed = json.loads(clean_text)
                except Exception as e:
                    # Fallback: extract first {...} blob if the model wrapped output
                    try:
                        mm = re.search(r"(?s)\{.*\}", clean_text)
                        if mm:
                            parsed = json.loads(mm.group(0))
                        else:
                            raise e
                    except Exception as e2:
                        parse_error = f"{type(e2).__name__}:{e2}"

                # Write structured insights (preferred)
                if isinstance(parsed, dict):
                    narrative = {
                        "version": "llm_insights_v2",
                        "provider": provider,
                        "model": os.environ.get("OLLAMA_MODEL", ""),
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                        **parsed,
                    }
                    debug["ok"] = True
                else:
                    # Fallback: keep text, but do not blow up the run
                    narrative = {
                        "version": "llm_insights_v2",
                        "provider": provider,
                        "model": os.environ.get("OLLAMA_MODEL", ""),
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                        "one_paragraph_summary": "",
                        "insights": [],
                        "risks_assumptions": [],
                        "next_analyses": [],
                        "signal_calls": [],
                        "fallback_text": clean_text,
                    }
                    debug["ok"] = False
                    debug["parsed_error"] = parse_error or "no_json_object_detected"

                # Debug metadata only (raw payload only if explicitly enabled)
                debug["model"] = os.environ.get("OLLAMA_MODEL", "")
                debug["url"] = os.environ.get("OLLAMA_URL", "")
                if os.environ.get("AURORA_LLM_DEBUG_RAW", "").strip() == "1":
                    debug["raw"] = resp
'''
    new_text = text[:m.start()] + m.group("prefix") + replacement + text[m.end():]
    return new_text

def patch_llm_file_names(text: str) -> str:
    # Ensure we write llm_narrative.json but now it contains structured insights;
    # (keep file name to avoid breaking downstream consumers)
    return text

def main() -> None:
    # parse args
    args = sys.argv[1:]
    if len(args) != 2 or args[0] != "--file":
        die("Usage: patch_runner_llm_insights_v2.py --file <path_to_runner.py>")

    path = Path(args[1])
    if not path.exists():
        die(f"File not found: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")

    # Step 1: ensure import re exists (your _strip_think uses it)
    text2 = ensure_import_re(text)

    # Step 2: patch _ollama_generate payload to force JSON + lower temp
    text3 = patch_ollama_payload(text2)

    # Step 3: patch prompt + parsing to structured insights
    text4 = patch_llm_prompt_and_parse(text3)

    # Step 4: write back
    path.write_text(text4, encoding="utf-8")
    print(f"[OK] patched -> {path}")

if __name__ == "__main__":
    main()

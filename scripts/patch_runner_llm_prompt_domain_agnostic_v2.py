from __future__ import annotations
import re, sys
from pathlib import Path
from datetime import datetime

def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}")
    sys.exit(code)

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        die(f"file not found: {p}")

    txt = p.read_text(encoding="utf-8")

    # --- backup ---
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{stamp}")
    bak.write_text(txt, encoding="utf-8")
    print(f"[OK] backup -> {bak}")

    # --- Patch A: add num_predict to Ollama options if missing (additive) ---
    if '"num_predict"' not in txt:
        txt2, n = re.subn(
            r'("options"\s*:\s*\{\s*\n)',
            r'\1            "num_predict": int(os.environ.get("AURORA_LLM_MAX_TOKENS", "1200")),\n',
            txt,
            count=1,
            flags=re.MULTILINE
        )
        if n == 0:
            print("[WARN] could not locate options:{ block; skipping num_predict insertion")
        else:
            txt = txt2
            print("[OK] inserted AURORA_LLM_MAX_TOKENS -> options.num_predict")
    else:
        print("[OK] num_predict already present; skipping")

    # --- Patch B: replace ONLY the prompt block inside provider == "ollama" ---
    # Anchor: inside the LLM section, locate 'if provider == "ollama":' ... 'resp = _ollama_generate(prompt)'
    # We replace the prompt assignment and ensure math_json/deep_json exist.

    block_pat = re.compile(
        r'(if\s+provider\s*==\s*"ollama"\s*:\s*\n)(.*?)(\n\s*resp\s*=\s*_ollama_generate\s*\(\s*prompt\s*\))',
        flags=re.DOTALL
    )
    m = block_pat.search(txt)
    if not m:
        die('could not locate provider=="ollama" block to patch.')

    head = m.group(1)
    body = m.group(2)
    tail = m.group(3)

    # Ensure helpers exist once
    helpers = (
        '                math_json = json.dumps(math_results, indent=2)[:9000]\\n'
        '                deep_json = json.dumps(deep_out, indent=2)[:9000]\\n'
    )
    if "math_json =" not in body:
        body = helpers + body
        print("[OK] inserted math_json/deep_json helpers")

    # Replace ONLY the prompt assignment if present; otherwise insert it near top of body.
    prompt_pat = re.compile(r'^\s*prompt\s*=\s*\(.*?^\s*\)\s*$', flags=re.DOTALL | re.MULTILINE)

    new_prompt = (
        '                prompt = (\\n'
        '                    "You are Aurora — a domain-agnostic quantitative analyst and scientific copilot.\\\\n"\\n'
        '                    "Aurora adapts to ANY dataset domain (finance, ops, medical, climate, web, etc.).\\\\n"\\n'
        '                    "You will be given structured engine outputs in JSON: math_results and deep_math.\\\\n\\\\n"\\n'
        '                    "TASK (GROUNDING REQUIRED):\\\\n"\\n'
        '                    "1) Infer the likely domain + what the dataset represents using ONLY the provided JSON.\\\\n"\\n'
        '                    "2) Produce specific insights grounded ONLY in math_results/deep_math (no hallucinations, no generic filler).\\\\n"\\n'
        '                    "3) Every insight MUST cite at least one concrete field/value you used (e.g., top_correlations[0], anomaly_count, regime_count, forecast_slope).\\\\n\\\\n"\\n'
        '                    "OUTPUT RULES (STRICT):\\\\n"\\n'
        '                    "- Return ONLY one valid JSON object. No markdown. No extra text.\\\\n"\\n'
        '                    "- Do NOT include <think> tags or hidden reasoning.\\\\n"\\n'
        '                    "- If signal is weak/missing, say so explicitly and propose next analyses.\\\\n\\\\n"\\n'
        '                    "JSON SCHEMA (MUST MATCH EXACTLY):\\\\n"\\n'
        '                    "{\\\\n"\\n'
        '                    \'  \\\\"one_paragraph_summary\\\\": \\\\"string\\\\",\\\\n\'\\n'
        '                    \'  \\\\"insights\\\\": [\\\\"5-10 crisp bullets; EACH bullet cites a key/value from inputs\\\\"],\\\\n\'\\n'
        '                    \'  \\\\"risks_assumptions\\\\": [\\\\"3-6 items; grounded\\\\"],\\\\n\'\\n'
        '                    \'  \\\\"next_analyses\\\\": [\\\\"3-8 items; concrete\\\\"],\\\\n\'\\n'
        '                    \'  \\\\"signal_calls\\\\": [\\\\"1-5 recommended actions / what to watch; grounded\\\\" ]\\\\n\'\\n'
        '                    "}\\\\n\\\\n"\\n'
        '                    "math_results:\\\\n"\\n'
        '                    + math_json\\n'
        '                    + "\\\\n\\\\n"\\n'
        '                    "deep_math:\\\\n"\\n'
        '                    + deep_json\\n'
        '                )\\n'
    )

    if "prompt =" in body:
        # Replace the first prompt=(...) block (best-effort)
        body2, n = re.subn(r'^\s*prompt\s*=\s*\(.*?^\s*\)\s*', new_prompt, body, count=1, flags=re.DOTALL | re.MULTILINE)
        if n == 0:
            # If prompt exists but doesn't match multiline shape, insert new prompt at top (keeps old prompt but will be overridden if later prompt is used).
            body = new_prompt + body
            print("[WARN] prompt existed but pattern did not match; inserted new prompt at top of block")
        else:
            body = body2
            print("[OK] replaced prompt with domain-agnostic grounded prompt")
    else:
        body = new_prompt + body
        print("[OK] inserted prompt into ollama block")

    new_block = head + body + tail
    txt = txt[:m.start()] + new_block + txt[m.end():]

    p.write_text(txt, encoding="utf-8")
    print(f"[DONE] wrote -> {p}")

if __name__ == "__main__":
    main()

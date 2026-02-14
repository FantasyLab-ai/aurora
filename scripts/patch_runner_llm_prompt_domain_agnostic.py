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

    # --- Patch A: add num_predict + keep options stable (safe, additive) ---
    # Find payload "options": { ... } inside _ollama_generate
    # Add num_predict if missing.
    if '"num_predict"' not in txt:
        txt2, n = re.subn(
            r'("options"\s*:\s*\{\s*\n\s*"temperature"\s*:\s*float\(os\.environ\.get\("AURORA_LLM_TEMP",\s*"0\.2"\)\)\s*,\s*\n)',
            r'\1            "num_predict": int(os.environ.get("AURORA_LLM_MAX_TOKENS", "900")),\n',
            txt,
            count=1,
            flags=re.MULTILINE
        )
        if n == 0:
            # If temperature line is different, try a softer insertion after options: {
            txt2, n = re.subn(
                r'("options"\s*:\s*\{\s*\n)',
                r'\1            "num_predict": int(os.environ.get("AURORA_LLM_MAX_TOKENS", "900")),\n',
                txt,
                count=1,
                flags=re.MULTILINE
            )
        txt = txt2
        print("[OK] inserted AURORA_LLM_MAX_TOKENS -> options.num_predict")
    else:
        print("[OK] num_predict already present; skipping")

    # --- Patch B: replace ONLY the prompt block (anchored) ---
    start = r'prompt\s*=\s*\('
    end   = r'\)\s*\n\s*\n\s*resp\s*=\s*_ollama_generate\s*\(\s*prompt\s*\)'
    m = re.search(start + r'(?s).*?' + end, txt)
    if not m:
        die("could not locate LLM prompt block to replace (anchor failed).")

    new_prompt = r'''prompt = (
                    "You are Aurora — a domain-agnostic quantitative analyst and scientific copilot.\\n"
                    "Aurora is NOT tied to environmental data; it must adapt to ANY dataset domain (finance, ops, medical, climate, web, etc.).\\n"
                    "You will be given structured engine outputs in JSON: math_results and deep_math.\\n\\n"

                    "TASK (GROUNDING REQUIRED):\\n"
                    "1) Infer the likely domain + what the dataset represents from column names/statistics ONLY.\\n"
                    "2) Produce an insight note grounded ONLY in math_results/deep_math (no hallucinated methods, no generic filler).\\n"
                    "3) Every insight MUST cite at least one concrete field/value you used (e.g., 'top_correlations[0]', 'anomalies_n', 'regime_count', 'forecast_slope').\\n\\n"

                    "OUTPUT RULES (STRICT):\\n"
                    "- Return ONLY one valid JSON object. No markdown. No extra keys.\\n"
                    "- Do NOT include <think> tags or hidden reasoning.\\n"
                    "- If signal is weak/missing, say so explicitly and propose next analyses.\\n\\n"

                    "JSON SCHEMA (MUST MATCH EXACTLY):\\n"
                    "{\\n"
                    '  \\"one_paragraph_summary\\": \\"string\\",\\n'
                    '  \\"insights\\": [\\"5-10 crisp bullets; EACH bullet cites a key/value from inputs\\"],\\n'
                    '  \\"risks_assumptions\\": [\\"3-6 items; grounded\\"],\\n'
                    '  \\"next_analyses\\": [\\"3-8 items; concrete\\"],\\n'
                    '  \\"signal_calls\\": [\\"1-5 recommended actions / what to watch; grounded\\" ]\\n'
                    "}\\n\\n"

                    "GROUNDING HINTS (USE IF PRESENT):\\n"
                    "- Use correlations, anomalies, regime shifts, forecast direction/bands, causal edges, missingness, strong drivers.\\n"
                    "- Prefer specifics over buzzwords. Avoid 'risk surface' unless it exists in inputs.\\n\\n"

                    "math_results:\\n"
                    + math_json
                    + "\\n\\n"
                    "deep_math:\\n"
                    + deep_json
                )
'''

    # We need to keep your slicing logic but make it cleaner:
    # Replace the old inline json.dumps(...) with precomputed safe strings so prompt is readable and stable.
    inject_helpers = r'''
                math_json = json.dumps(math_results, indent=2)[:9000]
                deep_json = json.dumps(deep_out, indent=2)[:9000]
'''
    # Insert helpers right before prompt assignment inside provider==ollama block.
    block = m.group(0)

    # If math_json/deep_json already exist, don't duplicate.
    if "math_json =" not in block:
        block2 = re.sub(r'(if\s+provider\s*==\s*"ollama"\s*:\s*\n)', r'\1' + inject_helpers, block, count=1)
    else:
        block2 = block

    # Now replace prompt assignment inside that block.
    block3, n2 = re.subn(start + r'(?s).*?(?=' + end + r')', new_prompt, block2, count=1)
    if n2 == 0:
        die("found block but failed to replace prompt assignment.")

    txt = txt[:m.start()] + block3 + txt[m.end():]
    print("[OK] replaced prompt block with domain-agnostic grounded schema")

    p.write_text(txt, encoding="utf-8")
    print(f"[DONE] wrote -> {p}")

if __name__ == "__main__":
    main()

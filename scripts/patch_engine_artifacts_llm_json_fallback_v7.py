from __future__ import annotations

import re
import json
import time
import shutil
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "engine_artifacts.py"


HELPER_BLOCK = r'''
def _extract_first_json_object(text: str):
    """
    Robustly extract the first valid JSON object from a messy LLM response.
    Returns (obj, err). If no obj, obj=None and err is a string.
    """
    if not isinstance(text, str) or not text.strip():
        return None, "empty_response"

    s = text.strip()

    # Fast path: exact JSON
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj, None
    except Exception:
        pass

    # Try to find the first balanced {...} region and parse it
    start = s.find("{")
    if start == -1:
        return None, "no_open_brace"

    # Balanced scan
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
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
                    candidate = s[start:i+1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj, None
                    except Exception as e:
                        return None, f"json_parse_failed:{e}"

    return None, "no_balanced_object"
'''.strip("\n")


FALLBACK_BLOCK = r'''
def _fallback_llm_summary(scorecard: dict, deep: dict) -> dict:
    """
    Deterministic fallback when the LLM does not return valid JSON.
    Produces a stable, UI-ready insights object grounded ONLY in scorecard/deep.
    """
    capabilities = (scorecard or {}).get("capabilities_executed") or {}
    findings = (scorecard or {}).get("key_findings") or {}
    risk = (scorecard or {}).get("risk_outlook") or {}

    failures = []
    causal = (scorecard or {}).get("causal_summary")
    if isinstance(causal, dict) and causal.get("error"):
        failures.append(str(causal.get("error")))

    largest_anom = findings.get("largest_anomaly_score")
    dom_reg = findings.get("dominant_regimes")
    strongest = findings.get("strongest_relationship")

    exec_lines = []
    exec_lines.append(f"Engine confidence: {scorecard.get('engine_confidence')}")
    if strongest and isinstance(strongest, dict):
        exec_lines.append(f"Strongest relationship: {strongest.get('a')} ↔ {strongest.get('b')} (corr={strongest.get('corr')})")
    if dom_reg is not None:
        exec_lines.append(f"Detected regimes: {dom_reg}")
    if largest_anom is not None:
        exec_lines.append(f"Largest anomaly score: {largest_anom}")
    if risk.get("uncertainty_model"):
        exec_lines.append(f"Risk model: {risk.get('uncertainty_model')} (horizon={risk.get('forecast_horizon')})")

    limitations = []
    if failures:
        limitations.append("One or more subsystems reported errors: " + "; ".join(failures))
    if not capabilities.get("causal_inference", True):
        limitations.append("Causal inference not executed or not successful for this dataset/run.")
    if not capabilities.get("forecasting", True):
        limitations.append("Forecasting not executed or not successful for this dataset/run.")

    steps = [
        "Confirm dataset time column + target selection for forecasting and causal analysis.",
        "Run the pipeline on 2–3 additional datasets to stress-test schema inference and missingness handling.",
        "Surface plots + scorecard + key drivers in the UI as a single 'Run Summary' panel.",
        "Add optional user controls: choose target metric, choose treatment variable, choose forecast horizon.",
    ]

    return {
        "executive_summary": " | ".join(exec_lines)[:1200],
        "scientific_summary": (
            "This run executed a domain-agnostic quantitative analysis stack including data validation, "
            "correlation discovery, anomaly detection, regime segmentation, forecasting with uncertainty, "
            "and optional causal/optimization modules. Outputs are derived from the scorecard and deep-math artifacts "
            "generated during the run, intended for UI-ready rendering."
        )[:2500],
        "limitations": limitations[:10],
        "recommended_next_steps": steps[:10],
        "evidence": {
            "capabilities_executed": capabilities,
            "key_findings": findings,
            "failures": failures,
        },
    }
'''.strip("\n")


def _backup(path: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_llmfixv7_{ts}")
    shutil.copy2(path, bak)
    return bak


def main():
    if not TARGET.exists():
        raise SystemExit(f"ERROR: not found: {TARGET}")

    src = TARGET.read_text(encoding="utf-8", errors="replace")

    # Ensure we actually have the function we intend to patch
    if "def generate_guarded_llm_summary" not in src:
        raise SystemExit("ERROR: generate_guarded_llm_summary not found. Restore engine_artifacts.py from a clean backup and retry.")

    bak = _backup(TARGET)
    print(f"[OK] backup -> {bak}")

    # 1) Inject helper + fallback near the top (after imports / before first function is fine)
    # We'll insert after the 'requests' optional import block to keep it organized.
    insert_anchor = "requests = None  # type: ignore"
    idx = src.find(insert_anchor)
    if idx == -1:
        # fallback: insert after last import
        m = re.search(r"(import requests.*?\n\s*requests\s*=\s*None.*?\n)", src, re.DOTALL)
        if m:
            idx = m.end()
        else:
            m2 = re.search(r"(import .*?\n)(?!import)", src)
            idx = m2.end() if m2 else 0
    else:
        # insert after that line
        idx = src.find("\n", idx)
        idx = idx + 1 if idx != -1 else len(src)

    additions = "\n\n" + HELPER_BLOCK + "\n\n" + FALLBACK_BLOCK + "\n\n"
    if "_extract_first_json_object" not in src:
        src = src[:idx] + additions + src[idx:]
        print("[OK] injected _extract_first_json_object + fallback summary")

    # 2) Replace brittle JSON parsing inside generate_guarded_llm_summary with robust extractor + fallback
    # We replace the whole "Best effort: parse JSON response" block.
    pattern = re.compile(
        r"(\n\s*# Best effort: parse JSON response\s*\n)(?P<body>.*?)(\n\s*_write_json\(run_dir\s*/\s*\"llm_narrative_guarded\.json\".*?\)\n)",
        re.DOTALL,
    )
    m = pattern.search(src)
    if not m:
        raise SystemExit("ERROR: could not locate the JSON parsing block in generate_guarded_llm_summary (unexpected file layout). Restore a clean backup and retry.")

    new_body = r"""
    # Best effort: parse JSON response (robust) + always-produce fallback insights
    parsed_obj = None
    parse_err = None
    if ok:
        parsed_obj, parse_err = _extract_first_json_object(txt)
        if parsed_obj is None:
            out["parsed_error"] = str(parse_err)

    # Always ensure we have a UI-usable parsed object
    if parsed_obj is None:
        out["parsed"] = _fallback_llm_summary(scorecard, deep)
        out["fallback_used"] = True
    else:
        out["parsed"] = parsed_obj
        out["fallback_used"] = False
""".strip("\n")

    # rebuild around the captured groups
    src = src[: m.start("body")] + new_body + src[m.end("body") :]
    print("[OK] replaced brittle JSON parsing with robust extractor + fallback")

    # 3) Write + compile check
    TARGET.write_text(src, encoding="utf-8")
    print(f"[OK] wrote -> {TARGET}")

    py_compile.compile(str(TARGET), doraise=True)
    print("[DONE] engine_artifacts.py compiled clean")


if __name__ == "__main__":
    main()

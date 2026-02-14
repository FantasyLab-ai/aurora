from __future__ import annotations

from pathlib import Path
import re
import py_compile

ROOT = Path(__file__).resolve().parents[1]

def find_engine_artifacts_py(root: Path) -> Path:
    # Common locations first
    candidates = [
        root / "engine_artifacts.py",
        root / "scripts" / "engine_artifacts.py",
        root / "fantasyai" / "engine_artifacts.py",
    ]
    for c in candidates:
        if c.exists():
            return c

    # Fallback: search entire repo
    hits = list(root.rglob("engine_artifacts.py"))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # Prefer one under scripts/ if multiple
        for h in hits:
            if "\\scripts\\" in str(h).lower() or "/scripts/" in str(h).lower():
                return h
        return hits[0]

    raise FileNotFoundError(f"engine_artifacts.py not found under {root}")

def backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".bak_llm_v3")
    bak.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return bak

def remove_bom_everywhere(s: str) -> str:
    return s.replace("\ufeff", "")

def main() -> None:
    ENGINE = find_engine_artifacts_py(ROOT)
    print(f"[INFO] patching -> {ENGINE}")

    backup(ENGINE)

    src = ENGINE.read_text(encoding="utf-8", errors="replace")
    src = remove_bom_everywhere(src)

    # Strict JSON-only prompt block (used if a PROMPT = f"""...""" exists)
    new_prompt = r'''PROMPT = f"""
You are Aurora, a Quantitative Intelligence Engine (QIE).

You MUST output EXACTLY ONE valid JSON object and NOTHING ELSE.
- No markdown
- No <think> tags
- No extra text
- Output must start with {{ and end with }}

JSON schema:

{{
  "headline": "one sentence, punchy and specific",
  "key_findings": [
    {{
      "finding": "specific quantified statement",
      "evidence": "cite exact fields/values from provided artifacts",
      "confidence": "high|medium|low"
    }}
  ],
  "risks": [
    {{
      "risk": "specific risk",
      "why": "specific reason tied to artifacts",
      "mitigation": "one concrete mitigation"
    }}
  ],
  "recommended_next_actions": [
    "3-7 concrete actions"
  ],
  "suggested_ui_tiles": [
    {{
      "tile": "UI tile name",
      "value": "number/string to show",
      "source": "artifact field path"
    }}
  ]
}}

INPUT ARTIFACTS:
- ENGINE_SCORECARD: {json.dumps(scorecard, indent=2)}
- DEEP_MATH: {json.dumps(deep_math, indent=2)}

Return JSON only.
""".strip()
'''

    # Replace PROMPT block if present (best-effort; safe if it doesn't match)
    prompt_pattern = r'PROMPT\s*=\s*f""".*?"""\.strip\(\)'
    if re.search(prompt_pattern, src, flags=re.DOTALL):
        src = re.sub(prompt_pattern, new_prompt, src, flags=re.DOTALL)
        print("[OK] updated PROMPT block to JSON-only schema")
    else:
        print("[WARN] PROMPT block not found (skipping prompt replacement)")

    # Inject deterministic fallback if missing
    if "_fallback_llm_summary" not in src:
        fallback_helper = r'''
def _fallback_llm_summary(scorecard: Dict[str, Any], deep_math: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic fallback so the UI always has usable insights even if the LLM returns non-JSON.
    """
    kf = (scorecard.get("key_findings") or {})
    ro = (scorecard.get("risk_outlook") or {})
    caps = (scorecard.get("capabilities_executed") or {})
    hc = (scorecard.get("health_checks") or {})

    headline = (
        f"Aurora analyzed {scorecard.get('dataset')} with "
        f"{scorecard.get('rows')} rows / {scorecard.get('cols')} cols; "
        f"regimes={kf.get('dominant_regimes')}, max_anomaly={kf.get('largest_anomaly_score')}."
    )

    findings = []
    sr = kf.get("strongest_relationship") or {}
    if sr.get("a") and sr.get("b") and sr.get("corr") is not None:
        try:
            r = float(sr["corr"])
            findings.append({
                "finding": f"Strongest correlation is {sr['a']} vs {sr['b']} (r={r:.3f}).",
                "evidence": "ENGINE_SCORECARD.key_findings.strongest_relationship",
                "confidence": "high"
            })
        except Exception:
            pass

    executed = [k for k, v in caps.items() if v]
    if executed:
        findings.append({
            "finding": f"Executed modules: {', '.join(executed)}.",
            "evidence": "ENGINE_SCORECARD.capabilities_executed",
            "confidence": "medium"
        })

    risks = []
    mf = hc.get("model_failures", 0) or 0
    if mf > 0:
        risks.append({
            "risk": "One or more model modules failed during execution.",
            "why": f"health_checks.model_failures={mf}",
            "mitigation": "Inspect deep_math.json sections with 'error' fields and rerun with stricter cleaning."
        })

    tiles = []
    if kf.get("target_col"):
        tiles.append({"tile": "Target", "value": str(kf.get("target_col")), "source": "ENGINE_SCORECARD.key_findings.target_col"})
    if ro.get("uncertainty_model"):
        tiles.append({"tile": "Risk Model", "value": str(ro.get("uncertainty_model")), "source": "ENGINE_SCORECARD.risk_outlook.uncertainty_model"})
    if ro.get("forecast_horizon") is not None:
        tiles.append({"tile": "Horizon", "value": str(ro.get("forecast_horizon")), "source": "ENGINE_SCORECARD.risk_outlook.forecast_horizon"})
    if kf.get("largest_anomaly_score") is not None:
        tiles.append({"tile": "Max Anomaly", "value": str(kf.get("largest_anomaly_score")), "source": "ENGINE_SCORECARD.key_findings.largest_anomaly_score"})

    return {
        "headline": headline,
        "key_findings": findings[:6],
        "risks": risks[:6],
        "recommended_next_actions": [
            "Open the plots (missingness/correlations/anomalies/regimes/forecast) and confirm they match expectations.",
            "If Date/Time exists, build a single datetime index for better forecasting and regime detection.",
            "Run causal what-if across 2–3 plausible treatment variables and sanity-check effect direction.",
            "Expose the scorecard + narrative as a single ‘Run Summary’ screen in the UI."
        ],
        "suggested_ui_tiles": tiles[:10]
    }
'''.strip()

        if "def generate_guarded_llm_summary" in src:
            src = src.replace("def generate_guarded_llm_summary", fallback_helper + "\n\n\ndef generate_guarded_llm_summary")
            print("[OK] injected fallback helper")
        else:
            print("[WARN] generate_guarded_llm_summary not found; fallback helper not injected")

    # Patch parse-failure paths (safe no-op if strings not present)
    if 'out["parsed_error"] = "no_json_object_detected"' in src and "fallback_used" not in src:
        src = src.replace(
            'out["parsed_error"] = "no_json_object_detected"',
            'out["parsed_error"] = "no_json_object_detected"\n                out["parsed"] = _fallback_llm_summary(scorecard, deep_math)\n                out["note"] = "fallback_used"'
        )
        print("[OK] added fallback on no_json_object_detected")

    if 'out["parsed_error"] = str(e)' in src and "fallback_used_exception" not in src:
        src = src.replace(
            'out["parsed_error"] = str(e)',
            'out["parsed_error"] = str(e)\n            out["parsed"] = _fallback_llm_summary(scorecard, deep_math)\n            out["note"] = "fallback_used_exception"'
        )
        print("[OK] added fallback on JSON parse exception")

    ENGINE.write_text(src, encoding="utf-8")
    py_compile.compile(str(ENGINE), doraise=True)
    print("[DONE] engine_artifacts.py compiled clean")

if __name__ == "__main__":
    main()

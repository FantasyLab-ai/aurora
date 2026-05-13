"""
Equation → plain-language translation.

Each translation file under ``equation_translations/`` carries:

  {
    "id": "damped_harmonic",
    "match": {
      "title_substrings": ["damped harmonic", "damped oscillator"],
      "method_substrings": ["physics_discovery", "ar2"],
      "equation_substrings": ["e^(-zt)", "exp(-z*t)"]
    },
    "param_to_intuition": [
      {
        "param": "zeta",
        "ranges": [
          {"min": 0.0, "max": 0.5,
           "phrase": "Underdamped — the system oscillates several times before settling."},
          {"min": 0.5, "max": 1.0,
           "phrase": "Moderately damped — visible overshoot before convergence."},
          {"min": 1.0, "max": 999.0,
           "phrase": "Overdamped — no oscillation; smooth convergence."}
        ]
      }
    ],
    "summary": "A second-order linear oscillator with damping ratio ζ and natural frequency ω."
  }

Translation result for a finding:

  {
    "form_id": "damped_harmonic",
    "summary": "A second-order linear oscillator…",
    "intuition_phrases": ["Underdamped — the system oscillates…",
                            "Natural period ≈ 9 time units."],
    "matched_via": "title_substrings"
  }

The translator never invents — when no rule matches, it returns None and
the finding card simply omits the translation chip.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional


def _norm_text(s: str) -> str:
    """Normalize a haystack for substring matching: lowercase, then map
    underscores / dashes to spaces. ``damped_harmonic_oscillator`` becomes
    ``damped harmonic oscillator``, so a rule's ``"damped harmonic"``
    substring still triggers."""
    if not s:
        return ""
    return s.lower().replace("_", " ").replace("-", " ")


def _matches_via(rule: Dict[str, Any],
                  finding: Dict[str, Any]) -> Optional[str]:
    """Did ``rule.match`` match this finding? Returns the channel name
    that triggered, or None."""
    match = rule.get("match") or {}
    title  = _norm_text(finding.get("title") or "")
    method = _norm_text(finding.get("method") or "")
    desc   = _norm_text(finding.get("description") or "")
    eqs: List[str] = []
    for k in ("equation", "equation_human"):
        v = finding.get(k)
        if v:
            eqs.append(_norm_text(str(v)))
    eq_text = " ".join(eqs)
    # Equation tokens (zeta / omega / param=value) appear in title or
    # description as often as in dedicated equation fields. Search there
    # too — the equation_substrings rule is a "do these terms appear
    # anywhere law-shaped?" check, not a strict equation-field probe.
    eq_haystack = (eq_text + " " + title + " " + desc).strip()

    for s in (match.get("title_substrings") or []):
        if _norm_text(s) in title:
            return "title_substrings"
    for s in (match.get("method_substrings") or []):
        if _norm_text(s) in method:
            return "method_substrings"
    for s in (match.get("desc_substrings") or []):
        if _norm_text(s) in desc:
            return "desc_substrings"
    for s in (match.get("equation_substrings") or []):
        if _norm_text(s) in eq_haystack:
            return "equation_substrings"
    return None


def _extract_param(finding: Dict[str, Any], name: str) -> Optional[float]:
    """Pull a numeric parameter from a finding by key.

    Looks in (in order): finding.params[name], finding[name],
    finding.law.params[name], finding.fitted_params[name].
    """
    candidates = []
    if isinstance(finding.get("params"), dict):
        candidates.append(finding["params"])
    if isinstance(finding.get("fitted_params"), dict):
        candidates.append(finding["fitted_params"])
    if isinstance(finding.get("law"), dict) and \
            isinstance(finding["law"].get("params"), dict):
        candidates.append(finding["law"]["params"])
    candidates.append(finding)
    for c in candidates:
        for key in (name, name.lower(), name.upper(),
                     name.replace("_", " "), name + "_value"):
            if isinstance(c, dict) and key in c:
                try:
                    v = float(c[key])
                    if math.isnan(v) or math.isinf(v):
                        continue
                    return v
                except Exception:
                    continue
    # Last resort: scrape title / description for "ζ=0.3" etc.
    text = (finding.get("title", "") + " "
            + finding.get("description", ""))
    m = re.search(rf"{re.escape(name)}\s*[=:]\s*(-?\d+(?:\.\d+)?)", text,
                    re.I)
    if not m:
        # Try common Greek symbol equivalents.
        symbol_map = {
            "zeta": "ζ", "omega": "ω", "tau": "τ",
            "alpha": "α", "beta": "β", "gamma": "γ",
            "phi": "φ", "sigma": "σ", "lambda": "λ",
        }
        sym = symbol_map.get(name.lower())
        if sym:
            m = re.search(rf"{sym}\s*[=:]\s*(-?\d+(?:\.\d+)?)", text)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def _phrase_for_param(param_def: Dict[str, Any],
                       value: float) -> Optional[str]:
    """Pick the range phrase for a parameter value."""
    for r in (param_def.get("ranges") or []):
        lo = r.get("min", float("-inf"))
        hi = r.get("max", float("inf"))
        if lo <= value < hi:
            return r.get("phrase")
    return None


def _derived_values(parameter_values: Dict[str, float]) -> Dict[str, str]:
    """Compute derived quantities (e.g. omega_period = 2π / omega) so
    range phrases that interpolate ``{omega_period}`` resolve cleanly.
    Returns a flat str→str map suitable for ``str.format_map``-style
    substitution."""
    out: Dict[str, str] = {}
    # Original values formatted with sensible precision.
    for k, v in parameter_values.items():
        out[k] = f"{v:.3g}"
    # Derived: omega_period — natural period for a damped harmonic.
    om = parameter_values.get("omega")
    if om is not None and om > 1e-9:
        out["omega_period"] = f"{(2 * math.pi / om):.2f}"
    # Derived: tau_inv — half-life-ish for exponential decays.
    tau = parameter_values.get("tau")
    if tau is not None and tau > 1e-9:
        out["tau_half_life"] = f"{(math.log(2) * tau):.2f}"
    return out


def _interpolate(phrase: str, derived: Dict[str, str]) -> str:
    """Substitute {param} placeholders in a phrase using ``derived``.
    Unknown placeholders are stripped (rather than left as ``{x}``)
    so the user never sees a raw template hole."""
    if not phrase or "{" not in phrase:
        return phrase
    def _sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        return derived.get(key, "")
    out = re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _sub, phrase)
    # Tidy whitespace artifacts when a placeholder vanished.
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out.strip()


def translate_equation(finding: Dict[str, Any],
                        translations: Dict[str, Dict[str, Any]]
                        ) -> Optional[Dict[str, Any]]:
    """Find the best matching translation rule and return its result.

    Returns ``{form_id, summary, intuition_phrases, matched_via,
    parameter_values}`` or ``None``.
    """
    if not finding or not translations:
        return None
    best: Optional[Dict[str, Any]] = None
    best_channel: Optional[str] = None
    for form_id, rule in translations.items():
        rule = dict(rule)
        rule.setdefault("id", form_id)
        ch = _matches_via(rule, finding)
        if not ch:
            continue
        # Prefer equation-text matches over title-substring matches when
        # multiple rules fire on the same finding.
        priority = {"equation_substrings": 3, "method_substrings": 2,
                     "desc_substrings": 1, "title_substrings": 0}
        cur_priority = priority.get(ch, 0)
        prev_priority = priority.get(best_channel, -1) if best else -1
        if best is None or cur_priority > prev_priority:
            best, best_channel = rule, ch
    if best is None:
        return None
    parameter_values: Dict[str, float] = {}
    raw_phrases: List[str] = []
    for param_def in (best.get("param_to_intuition") or []):
        pname = param_def.get("param")
        if not pname:
            continue
        val = _extract_param(finding, pname)
        if val is None:
            continue
        parameter_values[pname] = val
        phrase = _phrase_for_param(param_def, val)
        if phrase:
            raw_phrases.append(phrase)
    # Pass: substitute {omega_period}/{tau_half_life}/{value} placeholders
    # using derived quantities. Unknown placeholders get stripped.
    derived = _derived_values(parameter_values)
    intuition: List[str] = [_interpolate(p, derived) for p in raw_phrases]
    return {
        "form_id": best.get("id") or "",
        "summary": best.get("summary") or "",
        "intuition_phrases": intuition,
        "matched_via": best_channel,
        "parameter_values": parameter_values,
    }

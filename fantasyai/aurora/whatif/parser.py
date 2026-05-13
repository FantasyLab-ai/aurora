"""
Natural-language → InterventionSpec parser.

Two strategies, in this order:

1. **Regex pattern**: handles ~80% of common inputs without any LLM round-trip.
   Patterns recognized::

      "precip_mm +5"
      "precip_mm +5 for 12h"
      "precip_mm increases by 5"
      "if precip_mm drops by 3 for 2 hours"
      "set wind_speed to 25"
      "wind_speed = 25"

2. **LLM fallback** (Ollama if available): for free-form input the regex
   doesn't catch. Always returns a structured spec PLUS the raw_text, so the
   frontend can echo what was parsed and ask the user to confirm.

We never silently apply an LLM-parsed spec — confirmation step is critical
to avoid hallucinated counterfactuals.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .spec import InterventionSpec


# ----------------------------------------------------------------------
# Regex patterns
# ----------------------------------------------------------------------

# Match a variable name (word chars, dots, underscores).
_VAR = r"([A-Za-z_][A-Za-z0-9_\.]*)"
_NUM = r"([+-]?\d+(?:\.\d+)?)"

# Patterns ranked from most specific to most permissive.
_PATTERNS = [
    # "set X to V" or "X = V" → kind='set'
    (re.compile(rf"\b(?:set\s+)?{_VAR}\s*(?:=|to)\s*{_NUM}\b", re.IGNORECASE), "set"),
    # "if X drops by V" or "X drops V" → kind='delta', negative
    (re.compile(rf"\b{_VAR}\s+(?:drop(?:s|ped)?|decreas(?:es|ed))\s+(?:by\s+)?{_NUM}", re.IGNORECASE), "delta_neg"),
    # "if X increases by V" or "X increases V" → kind='delta', positive
    (re.compile(rf"\b{_VAR}\s+(?:rise(?:s)?|increas(?:es|ed)|gain(?:s|ed)?)\s+(?:by\s+)?{_NUM}", re.IGNORECASE), "delta_pos"),
    # "X +V" or "X -V" or "X +5"
    (re.compile(rf"\b{_VAR}\s*([+-])\s*{_NUM}\b"), "delta_signed"),
    # "X by V" (catch-all)
    (re.compile(rf"\b{_VAR}\s+by\s+{_NUM}\b", re.IGNORECASE), "delta"),
]

# Duration capture — runs separately on the same text.
_DURATION_RE = re.compile(
    rf"for\s+{_NUM}\s*(?:(s|sec|second|seconds|"
    rf"m|min|minute|minutes|"
    rf"h|hr|hour|hours|"
    rf"d|day|days|"
    rf"step|steps))",
    re.IGNORECASE,
)

# Units → time-step factor. The "step" unit is the runner's natural step.
# We default 1 step ≈ the dataset's dt_seconds; the simulator will pass that in.
_UNIT_TO_SECONDS: Dict[str, float] = {
    "s": 1, "sec": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}


def _parse_duration(text: str, dt_seconds: Optional[float]) -> Optional[int]:
    """Extract '...for X hours' → step count, given the run's dt_seconds.

    If unit is "step(s)", value is returned directly.
    If dt_seconds is unknown, fall back to interpreting raw value as steps.
    """
    m = _DURATION_RE.search(text)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("step", "steps"):
        return int(val)
    seconds = _UNIT_TO_SECONDS.get(unit, 1.0) * val
    if dt_seconds and dt_seconds > 0:
        return max(1, int(round(seconds / dt_seconds)))
    return int(val)  # fallback: interpret as raw step count


def _resolve_variable(name: str, available_columns: Optional[List[str]]) -> str:
    """If we have a column list, snap the parsed name to the closest case-insensitive match."""
    if not available_columns:
        return name
    name_lower = name.lower()
    for c in available_columns:
        if c.lower() == name_lower:
            return c
    # Substring match as fallback ("wind" → "wind_speed_mps")
    for c in available_columns:
        if name_lower in c.lower():
            return c
    return name  # leave as-is; identifiability check will catch it


# ----------------------------------------------------------------------
# Public entry — regex first, LLM fallback, always returns the raw_text
# ----------------------------------------------------------------------

def parse_intervention_text(
    text: str,
    *,
    available_columns: Optional[List[str]] = None,
    dt_seconds: Optional[float] = None,
    use_llm_fallback: bool = True,
) -> Tuple[Optional[InterventionSpec], str]:
    """Parse user text into an InterventionSpec.

    Returns:
        (spec, parser_used) — parser_used is one of: "regex", "llm", "none".
        If parsing fails entirely, returns (None, "none").
    """
    text = (text or "").strip()
    if not text:
        return None, "none"

    duration = _parse_duration(text, dt_seconds)

    # 1. Regex pass.
    for pattern, kind in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        var_raw = m.group(1)
        if kind == "set":
            value = float(m.group(2))
            spec = InterventionSpec(
                variable=_resolve_variable(var_raw, available_columns),
                kind="set", value=value,
                duration_steps=duration, raw_text=text,
            )
            return spec, "regex"
        if kind == "delta_signed":
            sign = m.group(2)
            value = float(m.group(3)) * (-1 if sign == "-" else 1)
            spec = InterventionSpec(
                variable=_resolve_variable(var_raw, available_columns),
                kind="delta", value=value,
                duration_steps=duration, raw_text=text,
            )
            return spec, "regex"
        if kind == "delta_neg":
            value = -abs(float(m.group(2)))
            spec = InterventionSpec(
                variable=_resolve_variable(var_raw, available_columns),
                kind="delta", value=value,
                duration_steps=duration, raw_text=text,
            )
            return spec, "regex"
        if kind == "delta_pos":
            value = abs(float(m.group(2)))
            spec = InterventionSpec(
                variable=_resolve_variable(var_raw, available_columns),
                kind="delta", value=value,
                duration_steps=duration, raw_text=text,
            )
            return spec, "regex"
        if kind == "delta":
            value = float(m.group(2))
            spec = InterventionSpec(
                variable=_resolve_variable(var_raw, available_columns),
                kind="delta", value=value,
                duration_steps=duration, raw_text=text,
            )
            return spec, "regex"

    # 2. LLM fallback — Ollama via local /api/chat. Optional.
    if use_llm_fallback:
        spec = _parse_via_llm(text, available_columns)
        if spec:
            return spec, "llm"

    return None, "none"


def _parse_via_llm(text: str, available_columns: Optional[List[str]]) -> Optional[InterventionSpec]:
    """Ask Ollama to structure free-form text into an intervention.

    The output is constrained: we ask the LLM for strict JSON with a known
    shape. If parsing fails or Ollama isn't running, return None.
    """
    try:
        import json
        import requests
    except ImportError:
        return None

    base = (os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "gemma3:12b")
    cols_hint = (
        f"\nThe dataset has these columns: {', '.join((available_columns or [])[:30])}"
        if available_columns else ""
    )
    system = (
        "You parse free-form 'what-if' interventions into strict JSON.\n"
        "Return ONLY a JSON object — no commentary, no prose — with this shape:\n"
        '{"variable": "<column>", "kind": "delta"|"set", "value": <number>, '
        '"duration_steps": <int or null>}\n'
        "If you cannot identify a clear variable + magnitude, return: "
        '{"variable": null}.\n'
        f"{cols_hint}"
    )
    try:
        r = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                "stream": False,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        msg = (data.get("message") or {}).get("content") or ""
        # Extract the first JSON object from the response.
        m = re.search(r"\{[\s\S]*\}", msg)
        if not m:
            return None
        parsed = json.loads(m.group(0))
        var = parsed.get("variable")
        if not var:
            return None
        return InterventionSpec(
            variable=_resolve_variable(str(var), available_columns),
            kind=str(parsed.get("kind") or "delta"),
            value=float(parsed.get("value") or 1.0),
            duration_steps=parsed.get("duration_steps") or None,
            raw_text=text,
        )
    except Exception:
        return None

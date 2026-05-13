"""
Infer SI unit + dimension from a column name.

Real-world column-naming conventions are messy but follow a few stable
patterns:

  *  ``temperature_c`` / ``temp_kelvin`` / ``T_K``     → suffix is the unit
  *  ``wind_speed_mps`` / ``wind_speed_ms``            → compound suffix
  *  ``pressure_hpa`` / ``pressure_pa``                → suffix
  *  ``vibration_g`` / ``acceleration_g0``             → ``g`` = gravity factor
  *  ``flow_rate_lpm`` / ``flow_lpm``                  → liters-per-minute
  *  ``rpm`` / ``frequency_hz``                        → SI symbol
  *  ``elapsed_seconds`` / ``time_s`` / ``timestamp``  → temporal axis
  *  ``mass_kg`` / ``weight_lbs``

We parse case-insensitively, prefer the longest suffix match, and tag a
``confidence`` score so downstream layers can decide whether to trust the
inference. Unknown columns get ``UnitGuess(None, None, 0.0)``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .dimensions import (
    Dimension, DIMENSIONLESS, dimension_from_unit_string,
    MASS, LENGTH, TIME, TEMPERATURE, CURRENT, AMOUNT, LUMINOUS,
    VELOCITY, ACCELERATION, FORCE, ENERGY, POWER, PRESSURE, FREQUENCY,
    AREA, VOLUME, DENSITY, CHARGE, VOLTAGE, RESISTANCE,
    HEAT_FLUX, MOLAR_RATE, CONCENTRATION,
)


@dataclass(frozen=True)
class UnitGuess:
    """Inferred unit metadata for a column."""
    unit: Optional[str]            # e.g. "celsius", "mps", "hpa", or None
    dimension: Optional[Dimension] # e.g. TEMPERATURE
    confidence: float              # 0..1
    source: str = "name"           # how we inferred (name | dtype | none)
    short_dim_name: str = ""       # human-readable like "velocity"

    def to_dict(self) -> Dict[str, Any]:
        d = {"unit": self.unit, "confidence": self.confidence,
             "source": self.source, "short_dim_name": self.short_dim_name}
        d["dimension"] = self.dimension.to_dict() if self.dimension else None
        return d


# ----------------------------------------------------------------------
# Pattern catalogue — ordered roughly by specificity.
# Each entry: (regex, dimension, unit_label, confidence).
# Longer / more-specific patterns come first.
# ----------------------------------------------------------------------

KNOWN_UNIT_PATTERNS: List[Tuple[re.Pattern, Dimension, str, float]] = [
    # ----- temperature -----
    (re.compile(r"(?:^|_)t(?:emp|emperature)?_?(?:c|celsius|deg_c|degc)\b", re.I),
     TEMPERATURE, "celsius", 0.95),
    (re.compile(r"(?:^|_)t(?:emp|emperature)?_?(?:f|fahrenheit|deg_f|degf)\b", re.I),
     TEMPERATURE, "fahrenheit", 0.95),
    (re.compile(r"(?:^|_)t(?:emp|emperature)?_?(?:k|kelvin)\b", re.I),
     TEMPERATURE, "kelvin", 0.95),
    (re.compile(r"\b(?:temp|temperature)\b", re.I),
     TEMPERATURE, "celsius", 0.7),

    # ----- velocity -----
    (re.compile(r"_(?:mps|m_s|m_per_s|mtrs?_per_s)\b", re.I),
     VELOCITY, "mps", 0.95),
    (re.compile(r"_(?:kmh|kph|km_h|km_per_h)\b", re.I),
     VELOCITY, "kph", 0.95),
    (re.compile(r"_mph\b", re.I), VELOCITY, "mph", 0.9),
    (re.compile(r"\b(?:wind_speed|velocity|speed)\b", re.I),
     VELOCITY, "mps", 0.7),

    # ----- acceleration -----
    (re.compile(r"_(?:g|g0|gforce|g_force)\b", re.I),
     ACCELERATION, "g0", 0.85),
    (re.compile(r"\b(?:acceleration|accel)\b", re.I),
     ACCELERATION, "mps2", 0.7),

    # ----- pressure -----
    (re.compile(r"_(?:hpa|kpa|mpa|pa|bar|atm|psi|mmhg)\b", re.I),
     PRESSURE, "MATCH", 0.95),
    (re.compile(r"\b(?:pressure|baro|barometric)\b", re.I),
     PRESSURE, "hpa", 0.7),

    # ----- frequency / rate -----
    (re.compile(r"_(?:hz|khz|mhz|ghz)\b", re.I),
     FREQUENCY, "MATCH", 0.95),
    (re.compile(r"\b(?:rpm|hz|frequency|freq)\b", re.I),
     FREQUENCY, "hz", 0.85),

    # ----- voltage / current / resistance -----
    (re.compile(r"_(?:mv|kv|v|volt|voltage)\b", re.I),
     VOLTAGE, "v", 0.9),
    (re.compile(r"_(?:ma|amp|ampere|current)\b", re.I),
     CURRENT, "a", 0.9),
    (re.compile(r"_(?:ohm|ohms|resistance)\b", re.I),
     RESISTANCE, "ohm", 0.9),

    # ----- mass -----
    (re.compile(r"_(?:kg|kilogram|grams?|mg|milligram|tonn?es?|lbs?|pound)\b", re.I),
     MASS, "MATCH", 0.9),
    (re.compile(r"\b(?:mass|weight)\b", re.I), MASS, "kg", 0.7),

    # ----- length -----
    (re.compile(r"_(?:km|kilometer|kilometre|m|meter|metre|cm|mm|ft|feet|foot|in|inch|mile|mi)\b", re.I),
     LENGTH, "MATCH", 0.85),
    (re.compile(r"\b(?:height|distance|wavelength|altitude|depth|wave_height)\b", re.I),
     LENGTH, "m", 0.7),

    # ----- time -----
    (re.compile(r"_(?:s|sec|second|seconds|min|minute|minutes|h|hr|hour|hours|day|days)\b", re.I),
     TIME, "MATCH", 0.9),
    (re.compile(r"\b(?:duration|elapsed|age_days?|wait_time|time_s)\b", re.I),
     TIME, "s", 0.75),
    # Use a custom boundary that matches underscore as a separator (since \b
    # treats underscore as a word char; "timestamp_utc" wouldn't match \btimestamp\b).
    (re.compile(r"(?:^|[^a-z0-9])(?:date|datetime|timestamp|time)(?:_[a-z0-9]+)?(?:$|[^a-z0-9])", re.I),
     TIME, "datetime", 0.85),

    # ----- volume / concentration -----
    (re.compile(r"\b(?:volume|liter|litre|gallon|cubic_meter)\b", re.I),
     VOLUME, "L", 0.7),
    (re.compile(r"_(?:mol_per_l|molarity|mg_per_l|g_per_l|mg_dl|mg_per_dl)\b", re.I),
     CONCENTRATION, "MATCH", 0.85),
    (re.compile(r"\b(?:concentration|cholesterol|biomarker)\b", re.I),
     CONCENTRATION, "mol_per_L", 0.6),

    # ----- power / energy -----
    (re.compile(r"_(?:w|watt|kw|mw|kwh|joule|kj|j)\b", re.I),
     POWER, "MATCH", 0.85),
    (re.compile(r"\b(?:power|watt)\b", re.I), POWER, "w", 0.7),
    (re.compile(r"\b(?:energy|kwh)\b", re.I), ENERGY, "j", 0.7),

    # ----- amount / count -----
    (re.compile(r"\b(?:count|n|num|number_of)\b", re.I),
     DIMENSIONLESS, "count", 0.65),

    # ----- ratios / pct (dimensionless) -----
    (re.compile(r"_(?:pct|percent|percentage|ratio|frac|fraction|prob)\b", re.I),
     DIMENSIONLESS, "pct", 0.85),
    (re.compile(r"\b(?:humidity|relative_humidity|moisture)\b", re.I),
     DIMENSIONLESS, "pct", 0.75),

    # ----- specific known column families (low-confidence catch-alls) -----
    (re.compile(r"\b(?:rpm)\b", re.I), FREQUENCY, "rpm", 0.9),
    (re.compile(r"\b(?:vibration|vib)\b", re.I), ACCELERATION, "g0", 0.7),
    (re.compile(r"\b(?:fetch|fetch_km)\b", re.I), LENGTH, "km", 0.85),
]


# ----------------------------------------------------------------------
# Inference
# ----------------------------------------------------------------------

def infer_column_unit(name: str) -> UnitGuess:
    """Infer the unit + dimension for a single column name.

    Returns ``UnitGuess(None, None, 0.0)`` when no pattern matches.
    """
    if not name or not isinstance(name, str):
        return UnitGuess(unit=None, dimension=None, confidence=0.0,
                         source="none", short_dim_name="")
    text = name.strip()

    for pattern, dim, label, conf in KNOWN_UNIT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        # If the unit label is "MATCH", recover the actual unit token from the regex match.
        if label == "MATCH":
            unit_token = (m.group(0).strip("_").lower())
            # Strip leading underscore artifacts from the regex.
            unit_token = unit_token.lstrip("_")
            actual_dim = dimension_from_unit_string(unit_token) or dim
            return UnitGuess(
                unit=unit_token, dimension=actual_dim, confidence=conf,
                source="name", short_dim_name=actual_dim.short_name(),
            )
        return UnitGuess(
            unit=label, dimension=dim, confidence=conf,
            source="name", short_dim_name=dim.short_name(),
        )

    return UnitGuess(unit=None, dimension=None, confidence=0.0,
                     source="none", short_dim_name="")


def infer_columns(names: List[str]) -> Dict[str, UnitGuess]:
    """Bulk-infer a list of column names. Returns dict keyed by original name."""
    return {n: infer_column_unit(n) for n in names}


def summarize_inference(guesses: Dict[str, UnitGuess]) -> Dict[str, Any]:
    """Quick stats: how many columns got dimensions, average confidence, etc."""
    n = len(guesses)
    n_with_dim = sum(1 for g in guesses.values() if g.dimension is not None)
    avg_conf = (sum(g.confidence for g in guesses.values()) / n) if n else 0.0
    by_dim: Dict[str, int] = {}
    for g in guesses.values():
        if g.dimension is None:
            continue
        key = g.short_dim_name or "unknown"
        by_dim[key] = by_dim.get(key, 0) + 1
    return {
        "total_columns": n,
        "with_dimension": n_with_dim,
        "without_dimension": n - n_with_dim,
        "average_confidence": avg_conf,
        "by_dimension": by_dim,
    }

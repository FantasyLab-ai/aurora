"""
SI dimension algebra.

A ``Dimension`` is a frozen tuple of exponents over the 7 SI base dimensions:

    (mass, length, time, temperature, current, amount, luminous_intensity)

Examples:

    velocity            = LENGTH / TIME              → (0,  1, -1, 0, 0, 0, 0)
    force               = MASS·LENGTH/TIME²          → (1,  1, -2, 0, 0, 0, 0)
    energy              = MASS·LENGTH²/TIME²         → (1,  2, -2, 0, 0, 0, 0)
    pressure            = MASS/(LENGTH·TIME²)        → (1, -1, -2, 0, 0, 0, 0)
    radiance / intensity= MASS/TIME³                 → (1,  0, -3, 0, 0, 0, 0)

Algebra: ``a * b``, ``a / b``, and ``a ** n`` produce new Dimensions
following exponent addition / subtraction / multiplication. This is the
math that backs Aurora's dimensional consistency check.

Hand-rolled (no external deps). When pint becomes a runtime dep we'll
swap the engine without changing the public API.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class Dimension:
    """Exponents over the 7 SI base dimensions."""
    mass: float = 0.0
    length: float = 0.0
    time: float = 0.0
    temperature: float = 0.0
    current: float = 0.0
    amount: float = 0.0
    luminous: float = 0.0

    # ----- algebra -----
    def __mul__(self, other: "Dimension") -> "Dimension":
        return Dimension(
            mass=self.mass + other.mass,
            length=self.length + other.length,
            time=self.time + other.time,
            temperature=self.temperature + other.temperature,
            current=self.current + other.current,
            amount=self.amount + other.amount,
            luminous=self.luminous + other.luminous,
        )

    def __truediv__(self, other: "Dimension") -> "Dimension":
        return Dimension(
            mass=self.mass - other.mass,
            length=self.length - other.length,
            time=self.time - other.time,
            temperature=self.temperature - other.temperature,
            current=self.current - other.current,
            amount=self.amount - other.amount,
            luminous=self.luminous - other.luminous,
        )

    def __pow__(self, n: float) -> "Dimension":
        return Dimension(
            mass=self.mass * n,
            length=self.length * n,
            time=self.time * n,
            temperature=self.temperature * n,
            current=self.current * n,
            amount=self.amount * n,
            luminous=self.luminous * n,
        )

    def is_dimensionless(self, tol: float = 1e-9) -> bool:
        return all(abs(getattr(self, f)) < tol for f in
                   ("mass", "length", "time", "temperature",
                    "current", "amount", "luminous"))

    def equals(self, other: "Dimension", tol: float = 1e-9) -> bool:
        return all(
            abs(getattr(self, f) - getattr(other, f)) < tol
            for f in ("mass", "length", "time", "temperature",
                      "current", "amount", "luminous")
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Dimension):
            return False
        return self.equals(other)

    def __hash__(self) -> int:
        return hash((self.mass, self.length, self.time, self.temperature,
                     self.current, self.amount, self.luminous))

    def to_tuple(self) -> Tuple[float, ...]:
        return (self.mass, self.length, self.time, self.temperature,
                self.current, self.amount, self.luminous)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mass": self.mass, "length": self.length, "time": self.time,
            "temperature": self.temperature, "current": self.current,
            "amount": self.amount, "luminous": self.luminous,
            "name": self.short_name(),
        }

    def short_name(self) -> str:
        """Return a human-readable name for common compound dimensions."""
        # Walk the registered named dimensions; first exact match wins.
        for name, d in _NAMED_DIMENSIONS:
            if self.equals(d):
                return name
        # Otherwise return a compact symbolic form.
        return _format_dimension(self)


# ----------------------------------------------------------------------
# Base dimensions (the 7 SI units)
# ----------------------------------------------------------------------

DIMENSIONLESS = Dimension()
MASS          = Dimension(mass=1)
LENGTH        = Dimension(length=1)
TIME          = Dimension(time=1)
TEMPERATURE   = Dimension(temperature=1)
CURRENT       = Dimension(current=1)
AMOUNT        = Dimension(amount=1)
LUMINOUS      = Dimension(luminous=1)


# ----------------------------------------------------------------------
# Named compound dimensions (used by short_name + the inference layer)
# ----------------------------------------------------------------------

VELOCITY      = LENGTH / TIME
ACCELERATION  = LENGTH / (TIME ** 2)
FORCE         = MASS * LENGTH / (TIME ** 2)
ENERGY        = MASS * (LENGTH ** 2) / (TIME ** 2)
POWER         = ENERGY / TIME
PRESSURE      = MASS / (LENGTH * (TIME ** 2))
FREQUENCY     = TIME ** -1
AREA          = LENGTH ** 2
VOLUME        = LENGTH ** 3
DENSITY       = MASS / VOLUME
CHARGE        = CURRENT * TIME
VOLTAGE       = ENERGY / CHARGE
RESISTANCE    = VOLTAGE / CURRENT
HEAT_FLUX     = POWER / AREA          # W/m²; Stefan-Boltzmann RHS
MOLAR_RATE    = AMOUNT / TIME         # mol/s; Michaelis-Menten v
CONCENTRATION = AMOUNT / VOLUME       # mol/m³

_NAMED_DIMENSIONS = [
    ("dimensionless", DIMENSIONLESS),
    ("mass",          MASS),
    ("length",        LENGTH),
    ("time",          TIME),
    ("temperature",   TEMPERATURE),
    ("current",       CURRENT),
    ("amount",        AMOUNT),
    ("luminous",      LUMINOUS),
    ("velocity",      VELOCITY),
    ("acceleration",  ACCELERATION),
    ("force",         FORCE),
    ("energy",        ENERGY),
    ("power",         POWER),
    ("pressure",      PRESSURE),
    ("frequency",     FREQUENCY),
    ("area",          AREA),
    ("volume",        VOLUME),
    ("density",       DENSITY),
    ("charge",        CHARGE),
    ("voltage",       VOLTAGE),
    ("resistance",    RESISTANCE),
    ("heat_flux",     HEAT_FLUX),
    ("molar_rate",    MOLAR_RATE),
    ("concentration", CONCENTRATION),
]


def _format_dimension(d: Dimension) -> str:
    """Compact symbolic rendering: M¹·L²·T⁻²  (only nonzero exponents)."""
    pairs = [
        ("M", d.mass), ("L", d.length), ("T", d.time),
        ("Θ", d.temperature), ("I", d.current),
        ("N", d.amount), ("J", d.luminous),
    ]
    parts = []
    for sym, exp in pairs:
        if abs(exp) < 1e-9:
            continue
        if abs(exp - 1.0) < 1e-9:
            parts.append(sym)
        elif abs(exp + 1.0) < 1e-9:
            parts.append(f"{sym}⁻¹")
        else:
            # Render fractional / negative exponents.
            exp_str = (f"{exp:g}").replace("-", "⁻")
            parts.append(f"{sym}^{exp_str}")
    return "·".join(parts) or "1"


# ----------------------------------------------------------------------
# Unit-string → Dimension parser
# ----------------------------------------------------------------------

# Map of common SI + derived unit symbols → Dimension.
# Both spelled-out names and short symbols are accepted; the longer matches
# win when scanning a column name (see inference.py).
_UNIT_STRING_TO_DIMENSION: Dict[str, Dimension] = {
    # base
    "kg": MASS,        "g": MASS,            "mg": MASS,
    "kilogram": MASS,  "gram": MASS,         "milligram": MASS,
    "m": LENGTH,       "km": LENGTH,         "cm": LENGTH,         "mm": LENGTH,
    "meter": LENGTH,   "metre": LENGTH,      "kilometer": LENGTH,  "kilometre": LENGTH,
    "ft": LENGTH,      "feet": LENGTH,       "foot": LENGTH,
    "in": LENGTH,      "inch": LENGTH,       "mile": LENGTH,        "mi": LENGTH,
    "s": TIME,         "sec": TIME,          "second": TIME,        "seconds": TIME,
    "min": TIME,       "minute": TIME,       "minutes": TIME,
    "h": TIME,         "hr": TIME,           "hour": TIME,          "hours": TIME,
    "day": TIME,       "days": TIME,         "week": TIME,          "weeks": TIME,
    "month": TIME,     "year": TIME,         "yr": TIME,
    "k": TEMPERATURE,  "kelvin": TEMPERATURE,
    "c": TEMPERATURE,  "celsius": TEMPERATURE,
    "f": TEMPERATURE,  "fahrenheit": TEMPERATURE,
    "a": CURRENT,      "amp": CURRENT,       "amps": CURRENT,       "ampere": CURRENT,
    "ma": CURRENT,     "milliamp": CURRENT,
    "mol": AMOUNT,     "mole": AMOUNT,
    "cd": LUMINOUS,    "candela": LUMINOUS,
    # derived
    "n": FORCE,        "newton": FORCE,      "kn": FORCE,
    "j": ENERGY,       "joule": ENERGY,      "kj": ENERGY,          "kwh": ENERGY,
    "w": POWER,        "watt": POWER,        "kw": POWER,           "mw": POWER,
    "pa": PRESSURE,    "pascal": PRESSURE,
    "hpa": PRESSURE,   "kpa": PRESSURE,      "mpa": PRESSURE,
    "bar": PRESSURE,   "atm": PRESSURE,      "psi": PRESSURE,       "mmhg": PRESSURE,
    "hz": FREQUENCY,   "khz": FREQUENCY,     "mhz": FREQUENCY,      "ghz": FREQUENCY,
    "rpm": FREQUENCY,
    "v": VOLTAGE,      "volt": VOLTAGE,      "kv": VOLTAGE,         "mv": VOLTAGE,
    "ohm": RESISTANCE, "ohms": RESISTANCE,
    # compound shortcuts (matched by name, not parsed)
    "mps": VELOCITY,   "kph": VELOCITY,      "kmh": VELOCITY,       "mph": VELOCITY,
    "g0": ACCELERATION,
}


def dimension_from_unit_string(unit_str: str) -> Optional[Dimension]:
    """Look up a unit string and return its dimension, or None if unknown.

    Case-insensitive. Empty / None input → None.
    """
    if not unit_str:
        return None
    key = unit_str.strip().lower()
    if not key:
        return None
    return _UNIT_STRING_TO_DIMENSION.get(key)

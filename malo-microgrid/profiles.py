#!/usr/bin/env python3
"""
Generation and demand profiles, with provenance tracking.

The point of this module is not the curves — it is the labelling. A result
computed on invented sunshine and a result computed on measured irradiance look
identical in a table, and quietly conflating them is how a research project
becomes unpublishable. So every profile carries a `Provenance` record stating
what it is and where it came from, that record travels into the run metrics,
and the console prints it at the top of every run.

Three sources, in descending order of credibility:

  MEASURED   a CSV you supply — PVWatts hourly export, a home-battery log,
             Pecan Street, your own inverter. Use this for anything you intend
             to publish.
  API        fetched live from NREL PVWatts (needs a free key and network).
             Real irradiance data for a real location and array.
  SYNTHETIC  a clear-sky physical model evaluated here. Solar geometry is
             exact — declination, hour angle, air mass — but the weather is
             invented, so it is honest about being a model, not a measurement.

The synthetic model is not a toy: it computes true solar position and applies a
Kasten-Young air-mass attenuation, so the diurnal shape and seasonal amplitude
are physically right. What it cannot know is whether it was cloudy.
"""

from __future__ import annotations

import csv
import math
import os
import random
from dataclasses import dataclass, field
from typing import Optional

SOLAR_CONSTANT_W = 1361.0      # W/m^2 at the top of the atmosphere
STC_IRRADIANCE_W = 1000.0      # W/m^2, the rating condition for a PV panel


# =============================================================================
# Provenance
# =============================================================================

@dataclass(frozen=True)
class Provenance:
    """Where a profile came from. Travels with the data into the metrics."""
    kind: str                  # "measured" | "api" | "synthetic"
    detail: str                # human-readable source description
    citable: bool              # may a published result rest on this?

    def __str__(self) -> str:
        mark = "" if self.citable else "  [NOT CITABLE — synthetic]"
        return f"{self.kind}: {self.detail}{mark}"


SYNTHETIC = "synthetic"
MEASURED = "measured"
API = "api"


@dataclass
class Profile:
    """An hourly series in kWh, plus where it came from."""
    values: list[float]
    provenance: Provenance
    label: str = ""

    def at(self, hour: int) -> float:
        """Hour-of-day lookup, wrapping so a run can exceed 24 blocks."""
        return self.values[hour % len(self.values)] if self.values else 0.0

    def scaled(self, factor: float) -> "Profile":
        return Profile([v * factor for v in self.values], self.provenance, self.label)


# =============================================================================
# Synthetic solar — real geometry, invented weather
# =============================================================================

def solar_position(day_of_year: int, hour: float, latitude_deg: float) -> float:
    """
    Cosine of the solar zenith angle. Negative means the sun is below the
    horizon. Standard declination/hour-angle formulation; accurate to well
    under a degree, which is far beyond what this simulation needs.
    """
    decl = math.radians(23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0)))
    hour_angle = math.radians(15.0 * (hour - 12.0))
    lat = math.radians(latitude_deg)
    return (math.sin(lat) * math.sin(decl)
            + math.cos(lat) * math.cos(decl) * math.cos(hour_angle))


def clear_sky_irradiance(cos_zenith: float) -> float:
    """
    Plane-of-array irradiance under a clear sky, W/m^2.

    Kasten-Young air mass with a simple atmospheric transmittance. Below the
    horizon returns zero; near the horizon the air mass grows sharply, which is
    what produces the realistic shoulder shape at dawn and dusk.
    """
    if cos_zenith <= 0.0:
        return 0.0
    zenith_deg = math.degrees(math.acos(min(1.0, cos_zenith)))
    air_mass = 1.0 / (cos_zenith + 0.50572 * (96.07995 - zenith_deg) ** -1.6364)
    return SOLAR_CONSTANT_W * 0.7 ** (air_mass ** 0.678) * cos_zenith


def synthetic_solar(capacity_kw: float, day_of_year: int = 172,
                    latitude_deg: float = 40.0, derate: float = 0.85,
                    cloudiness: float = 0.0,
                    rng: Optional[random.Random] = None) -> Profile:
    """
    24 hourly kWh values for a rooftop array.

    `cloudiness` in [0, 1] applies a random per-hour attenuation — 0 is a
    cloudless day, 0.5 is broken cloud. It is invented weather, which is
    precisely why the provenance says so.
    """
    r = rng or random.Random(0)
    values = []
    for hour in range(24):
        # Sample the middle of each hour rather than the boundary.
        poa = clear_sky_irradiance(solar_position(day_of_year, hour + 0.5, latitude_deg))
        attenuation = 1.0 - (r.uniform(0.0, cloudiness) if cloudiness > 0 else 0.0)
        values.append(round(capacity_kw * (poa / STC_IRRADIANCE_W) * derate * attenuation, 4))

    return Profile(values, Provenance(
        SYNTHETIC,
        f"clear-sky model, {capacity_kw:g} kW array, lat {latitude_deg:g}, "
        f"day {day_of_year}, cloudiness {cloudiness:g}",
        citable=False), label="solar")


# =============================================================================
# Synthetic demand — household base load and EV charging
# =============================================================================

# Normalised residential load shape (fraction of daily total per hour), the
# familiar overnight trough with a morning bump and a dominant evening peak.
_HOUSEHOLD_SHAPE = [
    0.025, 0.021, 0.019, 0.018, 0.019, 0.025,   # 00-05 overnight trough
    0.036, 0.047, 0.045, 0.038, 0.034, 0.033,   # 06-11 morning activity
    0.033, 0.032, 0.032, 0.035, 0.043, 0.058,   # 12-17 afternoon ramp
    0.070, 0.072, 0.065, 0.055, 0.043, 0.032,   # 18-23 evening peak
]


def synthetic_household(daily_kwh: float = 12.0,
                        jitter: float = 0.0,
                        rng: Optional[random.Random] = None) -> Profile:
    """Base household load, excluding any EV. Values are POSITIVE consumption."""
    r = rng or random.Random(0)
    # Normalise the shape so `daily_kwh` is the actual daily total, not an
    # approximation of it — the hand-written fractions do not sum to exactly 1.
    total = sum(_HOUSEHOLD_SHAPE)
    values = []
    for frac in _HOUSEHOLD_SHAPE:
        noise = 1.0 + (r.uniform(-jitter, jitter) if jitter else 0.0)
        values.append(round(daily_kwh * (frac / total) * noise, 4))
    return Profile(values, Provenance(
        SYNTHETIC, f"residential load shape, {daily_kwh:g} kWh/day", citable=False),
        label="household")


def synthetic_ev(battery_kwh: float = 60.0, charge_kw: float = 7.4,
                 arrival_hour: int = 17, target_soc: float = 0.8,
                 start_soc: float = 0.3,
                 rng: Optional[random.Random] = None) -> Profile:
    """
    Uncontrolled EV charging: plug in on arrival and draw full rated power until
    the target state of charge is reached. This is the worst case for the grid
    and exactly why local trading matters — every EV on the street starts at the
    same time, right at the evening peak.
    """
    needed = max(0.0, (target_soc - start_soc) * battery_kwh)
    values = [0.0] * 24
    hour, remaining = arrival_hour, needed
    while remaining > 1e-6 and hour < arrival_hour + 24:
        draw = min(charge_kw, remaining)
        values[hour % 24] += round(draw, 4)
        remaining -= draw
        hour += 1
    return Profile(values, Provenance(
        SYNTHETIC,
        f"uncontrolled EV charge, {battery_kwh:g} kWh pack at {charge_kw:g} kW "
        f"from {arrival_hour:02d}:00, SoC {start_soc:.0%}->{target_soc:.0%}",
        citable=False), label="ev")


# =============================================================================
# Measured data — CSV
# =============================================================================

def load_csv(path: str, column: Optional[str] = None, hour_column: Optional[str] = None,
             scale: float = 1.0, label: str = "") -> Profile:
    """
    Read an hourly profile from a CSV you supply. This is the path to a citable
    result: point it at a PVWatts hourly export, an inverter log, or a public
    dataset, and the provenance records the file.

    `column` names the value column; if omitted, the last numeric column is
    used. `hour_column` places values by hour-of-day; if omitted, rows are taken
    in order. Values are averaged when several rows land on the same hour, so a
    15-minute or 5-minute series downsamples correctly.
    """
    buckets: dict[int, list[float]] = {}
    with open(path, newline="") as fh:
        # PVWatts exports carry a preamble before the header; skip until a row
        # parses as a header with at least one numeric row after it.
        reader = csv.DictReader(_skip_preamble(fh))
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path}: no data rows found")

    field_names = [f for f in (rows[0].keys()) if f]
    if column is None:
        numeric = [f for f in field_names if _is_numeric(rows[0].get(f))]
        if not numeric:
            raise ValueError(f"{path}: no numeric column found in {field_names}")
        column = numeric[-1]

    for i, row in enumerate(rows):
        value = _to_float(row.get(column))
        if value is None:
            continue
        hour = int(_to_float(row.get(hour_column)) or 0) if hour_column else i
        buckets.setdefault(hour % 24, []).append(value * scale)

    values = [round(sum(buckets.get(h, [0.0])) / max(1, len(buckets.get(h, [0.0]))), 4)
              for h in range(24)]
    return Profile(values, Provenance(
        MEASURED, f"{os.path.basename(path)} column '{column}'", citable=True),
        label=label or os.path.basename(path))


def _skip_preamble(fh):
    """Yield lines from the first row that looks like a CSV header onwards."""
    lines = fh.read().splitlines()
    for i, line in enumerate(lines):
        cells = line.split(",")
        if len(cells) >= 2 and any(c.strip() and not _is_numeric(c) for c in cells):
            # A header row has several non-numeric cells; a data row does not.
            non_numeric = sum(1 for c in cells if c.strip() and not _is_numeric(c))
            if non_numeric >= max(1, len(cells) // 2):
                return lines[i:]
    return lines


def _is_numeric(value) -> bool:
    return _to_float(value) is not None


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


# =============================================================================
# Live data — NREL PVWatts
# =============================================================================

def fetch_pvwatts(api_key: str, lat: float, lon: float, system_capacity_kw: float = 4.0,
                  tilt: float = 20.0, azimuth: float = 180.0, array_type: int = 1,
                  module_type: int = 1, losses: float = 14.0,
                  day_of_year: int = 172, timeout: int = 30) -> Profile:
    """
    Pull one real day of hourly AC output from NREL PVWatts v8.

    Free key: https://developer.nrel.gov/signup/ — takes about a minute. This is
    the difference between "we modelled a sunny day" and "we used measured
    irradiance for Boulder, Colorado", and it is the single cheapest upgrade to
    the credibility of any result here.
    """
    import requests  # imported lazily: the rest of this module is offline-safe

    resp = requests.get(
        "https://developer.nrel.gov/api/pvwatts/v8.json",
        params={"api_key": api_key, "lat": lat, "lon": lon,
                "system_capacity": system_capacity_kw, "azimuth": azimuth, "tilt": tilt,
                "array_type": array_type, "module_type": module_type, "losses": losses,
                "timeframe": "hourly"},
        timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise ValueError(f"PVWatts: {body['errors']}")

    ac_watts = body["outputs"]["ac"]          # 8760 hourly values in W
    start = (day_of_year - 1) * 24
    day = ac_watts[start:start + 24]
    if len(day) < 24:
        raise ValueError(f"PVWatts returned {len(ac_watts)} hours; day {day_of_year} is out of range")

    station = body.get("station_info", {}).get("city", f"{lat},{lon}")
    return Profile([round(w / 1000.0, 4) for w in day], Provenance(
        API, f"NREL PVWatts v8, {station}, {system_capacity_kw:g} kW, day {day_of_year}",
        citable=True), label="solar")


# =============================================================================
# Provenance roll-up
# =============================================================================

def summarise_provenance(profiles: list[Profile]) -> dict:
    """
    Collapse a set of profiles into a single verdict for the run metrics.

    `citable` is true only if EVERY input is citable — one synthetic curve in
    the mix means the whole result is illustrative, and the run should say so
    rather than letting a reader assume otherwise.
    """
    if not profiles:
        return {"citable": False, "sources": [], "verdict": "no profile data"}
    sources = sorted({str(p.provenance) for p in profiles})
    citable = all(p.provenance.citable for p in profiles)
    return {
        "citable": citable,
        "sources": sources,
        "verdict": ("all inputs measured or from a data API — results are citable"
                    if citable else
                    "contains synthetic profiles — results are ILLUSTRATIVE ONLY"),
    }

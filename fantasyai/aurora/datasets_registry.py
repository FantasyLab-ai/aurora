from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List
import random

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "fantasyai" / "data" / "external"

@dataclass(frozen=True)
class DatasetSpec:
    key: str
    path: Path
    kind: str  # "tabular" | "timeseries"
    time_col: Optional[str] = None
    notes: str = ""

def _p(*parts: str) -> Path:
    return EXTERNAL.joinpath(*parts)

DATASETS: Dict[str, DatasetSpec] = {
    # You can add more keys as you download them:
    "uci_diabetes": DatasetSpec(
        key="uci_diabetes",
        path=_p("uci_diabetes", "raw"),
        kind="tabular",
        notes="UCI diabetes dataset (folder can contain one or more CSVs).",
    ),
    "cms_medicare": DatasetSpec(
        key="cms_medicare",
        path=_p("cms_medicare", "raw"),
        kind="tabular",
        notes="CMS Medicare PUF CSV(s).",
    ),
    "gaia": DatasetSpec(
        key="gaia",
        path=_p("gaia", "raw"),
        kind="tabular",
        notes="Gaia CSV exports (small samples recommended).",
    ),
    "goes_space_weather": DatasetSpec(
        key="goes_space_weather",
        path=_p("goes_space_weather", "raw"),
        kind="timeseries",
        time_col="time",
        notes="GOES-R space weather time series (CSV).",
    ),
}

def list_dataset_keys() -> List[str]:
    return sorted(DATASETS.keys())

def resolve_random_dataset(require_exists: bool = True) -> DatasetSpec:
    keys = list_dataset_keys()
    random.shuffle(keys)
    for k in keys:
        spec = DATASETS[k]
        if not require_exists:
            return spec
        if spec.path.exists() and any(spec.path.glob("*.csv")):
            return spec
    # fallback: return first even if empty
    return DATASETS[keys[0]]

def resolve_dataset(key: str) -> DatasetSpec:
    if key not in DATASETS:
        raise KeyError(f"Unknown dataset key: {key}. Known: {list_dataset_keys()}")
    return DATASETS[key]

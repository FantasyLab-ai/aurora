"""
OpenML datasets connector — free, no API key required.

API docs: https://www.openml.org/apis

OpenML hosts 20k+ curated ML datasets including the classics (iris, mnist,
adult, boston-housing) and tons of community uploads. Every dataset has
a stable numeric id; we fetch via:

  * Search: GET /api/v1/json/data/list/limit/{N}/{query_filters}
            (we use the ``data_name`` filter for keyword search)
  * Fetch:  download the parquet/arff/csv file for that dataset id

We download as ARFF (most reliable format on OpenML) and convert to CSV
in-process. Pandas can read ARFF via scipy.io.arff if needed, but we use
a minimal custom parser to avoid the dep.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, http_get_bytes, hash_bytes,
)

_BASE_URL = "https://www.openml.org/api/v1/json"


def _parse_arff(text: str) -> tuple[List[str], List[List[str]]]:
    """Minimal ARFF parser — extract column names + data rows."""
    headers: List[str] = []
    data_started = False
    rows: List[List[str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        if not data_started:
            ls = s.lower()
            if ls.startswith("@attribute"):
                # @attribute name {a, b, c}   or   @attribute name numeric
                m = re.match(r"@attribute\s+(['\"]?)([^'\"\s]+)\1\s+", s, re.I)
                if m:
                    headers.append(m.group(2))
            elif ls.startswith("@data"):
                data_started = True
            continue
        # Data section — naive CSV-style parse (OpenML ARFF is mostly clean)
        try:
            row = next(csv.reader([s]))
            rows.append(row)
        except Exception:
            continue
    return headers, rows


class OpenMLConnector(BaseConnector):
    id = "openml"
    display_name = "OpenML datasets"
    domain = "research"
    description = (
        "OpenML — 20,000+ curated ML datasets. Search by keyword. "
        "Examples: 'iris', 'titanic', 'diabetes', 'wine quality'. "
        "Each dataset has a stable id; data is downloaded as ARFF and "
        "auto-converted to CSV."
    )
    requires_api_key = False
    homepage_url = "https://www.openml.org/"
    cache_ttl_days = 90  # OpenML datasets are mostly static; long TTL

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        if not query:
            # Default: a curated short-list of beloved datasets
            curated = [
                ("61",   "iris",        "Classic 150-row 4-feature 3-class flower dataset (Fisher 1936)"),
                ("40945", "titanic",     "Passenger survival data — classification benchmark"),
                ("31",   "credit-g",    "German credit dataset — 1k rows, classification"),
                ("37",   "diabetes",    "Pima Indians diabetes — clinical biomarkers"),
                ("40996", "fashion-mnist","Fashion image classification (small subset)"),
                ("554",  "mnist_784",   "MNIST handwritten digits — 70k rows"),
                ("21",   "car",         "Car evaluation dataset"),
            ]
            return [
                ConnectorSearchResult(
                    item_id=did, title=name, description=desc,
                    rows_estimate=None, cols_estimate=None,
                    domain="research", source=self.id,
                    extras={"openml_id": did,
                            "url": f"https://www.openml.org/d/{did}"},
                )
                for did, name, desc in curated[:limit]
            ]

        try:
            data = http_get_json(
                f"{_BASE_URL}/data/list/limit/{limit}/data_name/{query}",
                timeout=15,
            )
        except Exception:
            # OpenML returns 412 if no datasets match — treat as empty results.
            return []
        items = (data.get("data", {}) or {}).get("dataset") or []
        if not isinstance(items, list):
            items = [items]
        results: List[ConnectorSearchResult] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            did = str(it.get("did", ""))
            if not did:
                continue
            results.append(ConnectorSearchResult(
                item_id=did,
                title=str(it.get("name", "")),
                description=(f"OpenML dataset #{did} · "
                             f"{it.get('NumberOfInstances', '?')} rows × "
                             f"{it.get('NumberOfFeatures', '?')} features · "
                             f"v{it.get('version', '?')}"),
                rows_estimate=int(it.get("NumberOfInstances", 0)) if str(it.get("NumberOfInstances", "")).isdigit() else None,
                cols_estimate=int(it.get("NumberOfFeatures", 0)) if str(it.get("NumberOfFeatures", "")).isdigit() else None,
                domain="research", source=self.id,
                extras={"openml_id": did, "format": it.get("format"),
                        "url": f"https://www.openml.org/d/{did}"},
            ))
        return results

    def _do_fetch(self, item_id: str) -> FetchPayload:
        # Get dataset metadata to find the parquet/arff URL.
        meta = http_get_json(f"{_BASE_URL}/data/{item_id}", timeout=20)
        ds = meta.get("data_set_description") or {}
        url = ds.get("url") or ds.get("parquet_url")
        if not url:
            raise ValueError(f"OpenML dataset {item_id} has no downloadable URL")

        # Try ARFF first (most universal). OpenML's `url` is the ARFF download.
        try:
            arff_bytes = http_get_bytes(url, timeout=120)
            text = arff_bytes.decode("utf-8", errors="replace")
            headers, rows = _parse_arff(text)
            if not headers or not rows:
                raise ValueError("ARFF parse produced no rows")
        except Exception as e:
            raise ValueError(f"OpenML fetch failed for {item_id}: {e}")

        # Build CSV
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(headers)
        for row in rows:
            # Pad / truncate row to header length for cleanliness.
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            elif len(row) > len(headers):
                row = row[:len(headers)]
            w.writerow(row)
        csv_bytes = buf.getvalue().encode("utf-8")

        return FetchPayload(
            csv_bytes=csv_bytes,
            rows=len(rows),
            cols=len(headers),
            response_hash=hash_bytes(arff_bytes),
            extras={
                "openml_id": item_id,
                "name": ds.get("name", ""),
                "description": (ds.get("description") or "")[:500],
                "version": ds.get("version", ""),
                "url": f"https://www.openml.org/d/{item_id}",
            },
        )

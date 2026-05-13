"""
BLS (US Bureau of Labor Statistics) connector — Atom 4.5.

Public-data API v1 requires no key for low-volume queries (≤25/day);
v2 supports a free key for higher limits. We use v1 by default and
gracefully escalate to v2 if a ``BLS_API_KEY`` is configured.

API docs: https://www.bls.gov/developers/api_signature_v2.htm

The connector pulls a single BLS series and writes it as a tidy CSV
with columns ``[year, period, value, series_id]``.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, hash_bytes,
)


BLS_BASE_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data"
BLS_BASE_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data"


# A small curated set of headline BLS series so the search() returns
# something useful without an API key. Real users specify a series id
# directly via the query.
_CURATED_SERIES = [
    {"id": "LNS14000000", "title": "Unemployment Rate (UNRATE)",
     "domain": "economics", "frequency": "monthly",
     "description": "Civilian unemployment rate, seasonally adjusted."},
    {"id": "CES0000000001", "title": "Total Nonfarm Payroll Employment",
     "domain": "economics", "frequency": "monthly",
     "description": "Total nonfarm employment, seasonally adjusted."},
    {"id": "CUUR0000SA0", "title": "Consumer Price Index (CPI-U)",
     "domain": "economics", "frequency": "monthly",
     "description": "All urban consumers, all items, not seasonally adjusted."},
    {"id": "CES0500000003", "title": "Avg Hourly Earnings, Total Private",
     "domain": "economics", "frequency": "monthly",
     "description": "Average hourly earnings of all employees, total private."},
    {"id": "WPSFD49207", "title": "Producer Price Index — Finished Goods",
     "domain": "economics", "frequency": "monthly",
     "description": "PPI for finished goods, not seasonally adjusted."},
]


class BLSConnector(BaseConnector):
    id = "bls"
    display_name = "BLS Labor Statistics"
    domain = "economics"
    description = (
        "U.S. Bureau of Labor Statistics — unemployment, CPI, wages, payrolls. "
        "v1 API key-free for ≤25 queries/day; set BLS_API_KEY for higher limits."
    )
    requires_api_key = False
    api_key_env_var = "BLS_API_KEY"
    homepage_url = "https://www.bls.gov/developers/"
    cache_ttl_days = 1   # BLS data updates daily/monthly

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        """Filter the curated series list by the query keywords. BLS doesn't
        expose a search endpoint; we ship a small whitelist that covers the
        canonical macro indicators."""
        q = (query or "").lower().strip()
        results: List[ConnectorSearchResult] = []
        for s in _CURATED_SERIES:
            haystack = f"{s['id']} {s['title']} {s['description']}".lower()
            if q and q not in haystack:
                continue
            results.append(ConnectorSearchResult(
                item_id=s["id"],
                title=s["title"],
                description=s["description"],
                rows_estimate=None,
                cols_estimate=4,
                domain=self.domain,
                source=self.id,
                extras={"frequency": s["frequency"]},
            ))
            if len(results) >= limit:
                break
        return results

    def _do_fetch(self, item_id: str) -> FetchPayload:
        """Fetch full available history of a single BLS series id."""
        url = BLS_BASE_V2 if self.has_api_key() else BLS_BASE_V1
        body: Dict[str, Any] = {"seriesid": [item_id]}
        if self.has_api_key():
            body["registrationkey"] = self._api_key()
        # http_get_json supports POST when ``json_body`` is supplied.
        try:
            data = http_get_json(url, json_body=body, method="POST")
        except TypeError:
            # Older base helper without method kwarg.
            import urllib.request
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        if not data or data.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(f"BLS API error: {data and data.get('message')}")

        series = (data.get("Results") or {}).get("series") or []
        if not series:
            raise RuntimeError(f"BLS returned no series for {item_id}")
        rows = []
        for s in series:
            sid = s.get("seriesID") or item_id
            for obs in (s.get("data") or []):
                rows.append({
                    "year": obs.get("year"),
                    "period": obs.get("period"),
                    "period_name": obs.get("periodName"),
                    "value": obs.get("value"),
                    "series_id": sid,
                })
        # BLS returns newest-first; sort chronologically.
        rows.sort(key=lambda r: (str(r["year"]), str(r["period"])))
        # Write CSV bytes.
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=[
            "year", "period", "period_name", "value", "series_id",
        ])
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")
        return FetchPayload(
            csv_bytes=csv_bytes,
            rows=len(rows),
            cols=5,
            response_hash=hash_bytes(csv_bytes),
            extras={"api_version": "v2" if self.has_api_key() else "v1",
                     "n_series": len(series)},
        )

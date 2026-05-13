"""
FRED (Federal Reserve Economic Data) connector.

FRED hosts ~800k macroeconomic time series (CPI, GDP, unemployment, fed
funds rate, treasury yields, etc.) — the canonical free source for U.S.
economic data. Returns native time series, perfect for Aurora's physics
discovery layer (logistic growth, regime shifts, oscillations).

API key
-------
Free key from https://fred.stlouisfed.org/docs/api/api_key.html. Set as
``FRED_API_KEY``. Without a key, ``has_api_key()`` returns False.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, hash_bytes,
)


FRED_BASE = "https://api.stlouisfed.org/fred"


class FREDConnector(BaseConnector):
    id = "fred"
    display_name = "FRED Economic Data"
    domain = "finance"
    description = (
        "U.S. Federal Reserve economic time series — GDP, CPI, unemployment, "
        "interest rates, ~800k series. Free key required."
    )
    requires_api_key = True
    api_key_env_var = "FRED_API_KEY"
    homepage_url = "https://fred.stlouisfed.org/docs/api/api_key.html"
    cache_ttl_days = 1   # macro data is updated daily/monthly

    def _common_params(self) -> Dict[str, Any]:
        return {"api_key": self._api_key() or "", "file_type": "json"}

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        if not self.has_api_key():
            return []
        params = {**self._common_params(), "search_text": query or "GDP",
                  "limit": min(limit, 25), "order_by": "popularity",
                  "sort_order": "desc"}
        data = http_get_json(f"{FRED_BASE}/series/search", params=params)
        results: List[ConnectorSearchResult] = []
        for s in (data or {}).get("seriess", [])[:limit]:
            sid = s.get("id", "")
            results.append(ConnectorSearchResult(
                item_id=sid,
                title=s.get("title") or sid,
                description=(
                    f"{s.get('frequency_short','?')} · "
                    f"{s.get('observation_start','?')} → {s.get('observation_end','?')} · "
                    f"{s.get('units_short','')}"
                ),
                rows_estimate=None,
                cols_estimate=2,   # date + value
                domain=self.domain,
                source=self.id,
                extras={
                    "frequency": s.get("frequency"),
                    "units": s.get("units"),
                    "seasonal_adjustment": s.get("seasonal_adjustment"),
                    "last_updated": s.get("last_updated"),
                    "popularity": s.get("popularity"),
                },
            ))
        return results

    def _do_fetch(self, item_id: str) -> FetchPayload:
        """Fetch the full observation history for a FRED series id."""
        if not self.has_api_key():
            raise RuntimeError("FRED_API_KEY not set; cannot fetch.")
        params = {**self._common_params(), "series_id": item_id, "limit": 100000}
        data = http_get_json(f"{FRED_BASE}/series/observations", params=params)
        obs = (data or {}).get("observations") or []
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["date", item_id])
        n = 0
        for o in obs:
            d = o.get("date", "")
            v = o.get("value", "")
            # FRED uses "." for missing — keep as empty.
            if v == ".":
                v = ""
            writer.writerow([d, v])
            n += 1
        csv_bytes = out.getvalue().encode("utf-8")
        return FetchPayload(
            csv_bytes=csv_bytes,
            rows=n,
            cols=2,
            response_hash=hash_bytes(csv_bytes),
            extras={"series_id": item_id},
        )

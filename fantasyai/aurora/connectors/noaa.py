"""
NOAA Climate Data Online (CDO) connector.

NOAA provides decades of weather observations (temperature, precipitation,
wind, snow) from thousands of stations. The CDO v2 REST API returns
station/dataset/data records as JSON.

API key
-------
NOAA gives a free key at https://www.ncdc.noaa.gov/cdo-web/token. Set it as
``NOAA_CDO_API_KEY``. Without a key, ``has_api_key()`` returns False and the
API endpoints respond with 400 + a "set this env var" hint.

Why CDO over the bulk-tarball method
------------------------------------
For a connector we want preview-search → fetch-on-click. CDO's REST API does
exactly that, while the bulk archive (CDO global summaries .csv.gz) is
hundreds of MB per file. Trade-off: CDO REST is rate-limited (5 req/s, 10k
req/day), so we cache every fetch.

What we return
--------------
A wide CSV with columns:
    date, station_id, data_type_id, value, attributes
Aurora's runner can then auto-pick a numeric column and a date axis.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Dict, List

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, hash_bytes,
)


CDO_BASE = "https://www.ncei.noaa.gov/cdo-web/api/v2"


class NOAAConnector(BaseConnector):
    id = "noaa_cdo"
    display_name = "NOAA Climate Data Online (CDO)"
    domain = "enviro"
    description = (
        "Daily/hourly weather observations (TMAX, TMIN, PRCP, WIND) from NOAA's "
        "global station network. Free key required."
    )
    requires_api_key = True
    api_key_env_var = "NOAA_CDO_API_KEY"
    homepage_url = "https://www.ncdc.noaa.gov/cdo-web/token"
    cache_ttl_days = 30   # weather history doesn't change

    def _headers(self) -> Dict[str, str]:
        key = self._api_key() or ""
        return {"token": key}

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        """Search NOAA stations matching the query string.

        Returns one preview per station — fetching that station_id pulls all
        daily summaries for the past year (capped at NOAA's 1000-record limit).
        """
        if not self.has_api_key():
            return []
        # NOAA station search uses the /stations endpoint with a ?searchstring=
        params = {
            "datasetid": "GHCND",         # daily summaries
            "limit": min(limit, 25),
            "sortfield": "name",
        }
        if query:
            params["searchstring"] = query
        data = http_get_json(f"{CDO_BASE}/stations",
                              params=params, headers=self._headers())
        results: List[ConnectorSearchResult] = []
        for s in (data or {}).get("results", [])[:limit]:
            sid = s.get("id", "")
            results.append(ConnectorSearchResult(
                item_id=sid,
                title=s.get("name") or sid,
                description=(
                    f"{s.get('datacoverage','?')*100:.0f}% coverage · "
                    f"{s.get('mindate','?')} → {s.get('maxdate','?')}"
                ) if isinstance(s.get("datacoverage"), (int, float)) else
                    f"{s.get('mindate','?')} → {s.get('maxdate','?')}",
                rows_estimate=None,
                cols_estimate=5,
                domain=self.domain,
                source=self.id,
                extras={
                    "elevation": s.get("elevation"),
                    "latitude": s.get("latitude"),
                    "longitude": s.get("longitude"),
                    "mindate": s.get("mindate"),
                    "maxdate": s.get("maxdate"),
                },
            ))
        return results

    def _do_fetch(self, item_id: str) -> FetchPayload:
        """Fetch the past 365 days of daily summaries for the given station_id.

        item_id is the NOAA station id (e.g. ``GHCND:USW00094728`` for NYC Central Park).
        """
        if not self.has_api_key():
            raise RuntimeError("NOAA_CDO_API_KEY not set; cannot fetch.")
        # NOAA limits each request to 1000 records. Pull the most recent year.
        import datetime as _dt
        end = _dt.date.today()
        start = end - _dt.timedelta(days=365)
        params = {
            "datasetid": "GHCND",
            "stationid": item_id,
            "startdate": start.isoformat(),
            "enddate": end.isoformat(),
            "limit": 1000,
            "units": "metric",
        }
        data = http_get_json(f"{CDO_BASE}/data", params=params, headers=self._headers())
        rows = (data or {}).get("results") or []
        # Pivot date → wide format so the runner sees a tidy time series.
        # Columns: date, then one column per data_type (TMAX, TMIN, PRCP, ...)
        by_date: Dict[str, Dict[str, Any]] = {}
        types: List[str] = []
        for r in rows:
            dt = (r.get("date") or "")[:10]   # 'YYYY-MM-DD'
            tp = r.get("datatype", "")
            v = r.get("value")
            if dt not in by_date:
                by_date[dt] = {"date": dt, "station": item_id}
            if tp:
                if tp not in types:
                    types.append(tp)
                by_date[dt][tp] = v
        # Build CSV.
        fieldnames = ["date", "station"] + types
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        for dt in sorted(by_date.keys()):
            writer.writerow({k: by_date[dt].get(k, "") for k in fieldnames})
        csv_bytes = out.getvalue().encode("utf-8")
        return FetchPayload(
            csv_bytes=csv_bytes,
            rows=len(by_date),
            cols=len(fieldnames),
            response_hash=hash_bytes(csv_bytes),
            extras={"types": types, "start": start.isoformat(), "end": end.isoformat()},
        )

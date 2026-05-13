"""
USGS Earthquake Catalog connector — free, no API key required, no rate limit.

API docs: https://earthquake.usgs.gov/fdsnws/event/1/

Returns earthquakes worldwide. Rich query parameters (time range, magnitude,
geographic box). Default search returns recent significant quakes (M≥4.5
in the last 30 days).

We bypass the search → fetch split somewhat: each "search result" is itself a
ready-to-fetch query slug. Aurora's UX: user picks "earthquakes magnitude
≥ 5 in California, 2024" → sees count preview → confirms → CSV downloaded.
"""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, hash_bytes,
)

_BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_COUNT_URL = "https://earthquake.usgs.gov/fdsnws/event/1/count"


def _build_query(query: str) -> Dict[str, Any]:
    """Translate a user-friendly search string into USGS query parameters.

    Recognized tokens:
      * ``mag>5``          → minmagnitude
      * ``last:30d``       → starttime = now - 30d
      * ``year:2024``      → starttime / endtime for that calendar year
      * ``california``     → bounding box for CA
      * ``japan``          → bounding box for Japan
      * (otherwise treated as full-text in the place description, USGS doesn't
        support text search natively, so we fetch globally and filter.)
    """
    params: Dict[str, Any] = {"format": "geojson", "limit": 200}
    q = (query or "").lower()

    # Magnitude
    import re
    m = re.search(r"mag\s*>=?\s*(\d+(?:\.\d+)?)", q)
    if m:
        params["minmagnitude"] = float(m.group(1))
    elif "significant" in q:
        params["minmagnitude"] = 4.5

    # Time window
    now = datetime.now(timezone.utc)
    if "last:30d" in q or "last 30" in q or "month" in q:
        params["starttime"] = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    elif "last:7d" in q or "week" in q:
        params["starttime"] = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    elif "last:1d" in q or "today" in q:
        params["starttime"] = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    elif "year:" in q:
        m = re.search(r"year:(\d{4})", q)
        if m:
            yr = int(m.group(1))
            params["starttime"] = f"{yr}-01-01"
            params["endtime"] = f"{yr}-12-31"

    # Default time window if none specified
    if "starttime" not in params:
        params["starttime"] = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    # Geographic bounding boxes (simple regions)
    bboxes = {
        "california":  {"minlatitude": 32, "maxlatitude": 42,
                        "minlongitude": -125, "maxlongitude": -114},
        "japan":       {"minlatitude": 24, "maxlatitude": 46,
                        "minlongitude": 122, "maxlongitude": 146},
        "alaska":      {"minlatitude": 51, "maxlatitude": 72,
                        "minlongitude": -180, "maxlongitude": -130},
        "italy":       {"minlatitude": 36, "maxlatitude": 48,
                        "minlongitude": 6, "maxlongitude": 19},
        "indonesia":   {"minlatitude": -11, "maxlatitude": 6,
                        "minlongitude": 95, "maxlongitude": 141},
        "us":          {"minlatitude": 24, "maxlatitude": 50,
                        "minlongitude": -125, "maxlongitude": -66},
    }
    for region, bbox in bboxes.items():
        if region in q:
            params.update(bbox)
            break
    return params


class USGSEarthquakeConnector(BaseConnector):
    id = "usgs_earthquake"
    display_name = "USGS Earthquake Catalog"
    domain = "enviro"
    description = (
        "USGS earthquake catalog — every quake worldwide, real-time. Query by "
        "magnitude, time window, region. Examples: 'last 30d', 'california "
        "mag>4', 'year:2024 japan'."
    )
    requires_api_key = False
    homepage_url = "https://earthquake.usgs.gov/earthquakes/search/"
    cache_ttl_days = 1   # earthquake data is incremental; refresh daily

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        params = _build_query(query)

        # Get count first (cheap call, separate USGS endpoint)
        try:
            count_data = http_get_json(_COUNT_URL, params=params, timeout=10)
            count = int(count_data.get("count", 0)) if isinstance(count_data, dict) else 0
        except Exception:
            count = -1  # unknown

        # Build a single search-result preview describing this query.
        title_bits: List[str] = []
        if "minmagnitude" in params:
            title_bits.append(f"M≥{params['minmagnitude']}")
        if "starttime" in params and "endtime" in params:
            title_bits.append(f"{params['starttime']} to {params['endtime']}")
        elif "starttime" in params:
            title_bits.append(f"since {params['starttime']}")
        title = "Earthquakes: " + " · ".join(title_bits) if title_bits else "Earthquakes (default)"

        item_id = urlencode(sorted(params.items()))
        return [ConnectorSearchResult(
            item_id=item_id,
            title=title,
            description=(
                f"~{count if count >= 0 else 'unknown'} events match this query. "
                f"Bounding box / region applied as parsed."
            ),
            rows_estimate=count if count >= 0 else None,
            cols_estimate=8,
            domain="enviro", source=self.id,
            extras={"query_params": params, "count": count},
        )]

    def _do_fetch(self, item_id: str) -> FetchPayload:
        # item_id is the urlencoded query string we returned from search.
        from urllib.parse import parse_qsl
        params = dict(parse_qsl(item_id))
        # USGS limits to 20000 results per query; clamp.
        if "limit" in params:
            try:
                params["limit"] = str(min(int(params["limit"]), 20000))
            except Exception:
                params["limit"] = "1000"
        else:
            params["limit"] = "1000"

        data = http_get_json(_BASE_URL, params=params, timeout=60)
        features = data.get("features") or []
        if not features:
            raise ValueError("USGS query returned no events")

        # Flatten GeoJSON features into a tabular CSV.
        headers = [
            "time_iso", "time_unix_ms", "place", "magnitude", "mag_type",
            "depth_km", "latitude", "longitude",
            "felt", "tsunami", "alert", "url",
        ]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(headers)
        for f in features:
            if not isinstance(f, dict):
                continue
            props = f.get("properties") or {}
            geom = (f.get("geometry") or {}).get("coordinates") or []
            lon = geom[0] if len(geom) > 0 else ""
            lat = geom[1] if len(geom) > 1 else ""
            depth = geom[2] if len(geom) > 2 else ""
            t_ms = props.get("time")
            t_iso = ""
            if isinstance(t_ms, (int, float)):
                try:
                    t_iso = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc) \
                                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass
            w.writerow([
                t_iso, t_ms, props.get("place", ""),
                props.get("mag", ""), props.get("magType", ""),
                depth, lat, lon,
                props.get("felt", ""), props.get("tsunami", ""),
                props.get("alert", ""), props.get("url", ""),
            ])
        csv_bytes = buf.getvalue().encode("utf-8")

        return FetchPayload(
            csv_bytes=csv_bytes,
            rows=len(features),
            cols=len(headers),
            response_hash=hash_bytes(csv_bytes),
            extras={"feature_count": len(features), "query_params": params},
        )

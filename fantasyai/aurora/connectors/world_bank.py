"""
World Bank Open Data Indicators connector — free, no API key required.

API docs: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392

We hit two endpoints:

  * Search:  GET /v2/indicator?format=json&per_page=…&search=…
             returns a list of indicators matching a keyword
  * Fetch:   GET /v2/country/{country_codes}/indicator/{indicator_id}?format=json
             returns the time-series data for an indicator across countries

The fetch returns one row per (country, year). We pivot to wide-form CSV
(years as rows, countries as columns) which is what Aurora's runner
prefers for time-series analysis.

Defaults: when ``item_id`` is just an indicator code (e.g. ``NY.GDP.MKTP.CD``),
we fetch for ``USA`` only. To fetch a different scope, pass
``"NY.GDP.MKTP.CD:USA;CHN;DEU"`` etc.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, hash_bytes,
)


_BASE_URL = "https://api.worldbank.org/v2"


class WorldBankConnector(BaseConnector):
    id = "world_bank"
    display_name = "World Bank Open Data"
    domain = "finance"
    description = (
        "World Bank Indicators API — macroeconomic + social data for ~250 "
        "countries, deep history. Examples: GDP, inflation, unemployment, "
        "population, life expectancy."
    )
    requires_api_key = False
    homepage_url = "https://data.worldbank.org/"
    cache_ttl_days = 30

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        if not query:
            # Default popular indicators when no query.
            popular = [
                ("NY.GDP.MKTP.CD",       "GDP (current US$)"),
                ("FP.CPI.TOTL.ZG",       "Inflation, consumer prices (annual %)"),
                ("SL.UEM.TOTL.ZS",       "Unemployment, total (% of labor force)"),
                ("SP.POP.TOTL",          "Population, total"),
                ("NY.GDP.PCAP.CD",       "GDP per capita (current US$)"),
                ("EG.USE.ELEC.KH.PC",    "Electric power consumption (kWh per capita)"),
                ("EN.ATM.CO2E.PC",       "CO2 emissions (metric tons per capita)"),
                ("SP.DYN.LE00.IN",       "Life expectancy at birth, total (years)"),
            ]
            return [
                ConnectorSearchResult(
                    item_id=code, title=name,
                    description=f"World Bank indicator {code} (default: USA, last 60 yrs)",
                    rows_estimate=60, cols_estimate=2,
                    domain="finance", source=self.id,
                    extras={"indicator_code": code, "default_country": "USA"},
                )
                for code, name in popular[:limit]
            ]

        # Live search
        try:
            data = http_get_json(
                f"{_BASE_URL}/indicator",
                params={"format": "json", "per_page": min(limit, 50),
                        "source": "2",  # World Development Indicators
                        "search": query},
                timeout=15,
            )
        except Exception as e:
            return []
        # Response shape: [meta_dict, [indicator_dict, ...]]
        if not isinstance(data, list) or len(data) < 2:
            return []
        items = data[1] or []
        results: List[ConnectorSearchResult] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            results.append(ConnectorSearchResult(
                item_id=it.get("id", ""),
                title=it.get("name", "(unnamed)"),
                description=(it.get("sourceNote") or "")[:200],
                rows_estimate=60, cols_estimate=2,
                domain="finance", source=self.id,
                extras={
                    "indicator_code": it.get("id"),
                    "topics": [t.get("value") for t in (it.get("topics") or [])
                               if isinstance(t, dict)],
                    "default_country": "USA",
                },
            ))
        return results

    def _do_fetch(self, item_id: str) -> FetchPayload:
        # item_id forms:
        #   "NY.GDP.MKTP.CD"             → USA only
        #   "NY.GDP.MKTP.CD:USA"          → explicit single country
        #   "NY.GDP.MKTP.CD:USA;CHN;DEU"  → multiple countries pivoted to wide
        if ":" in item_id:
            indicator, country_part = item_id.split(":", 1)
            countries = [c.strip().upper() for c in country_part.split(";") if c.strip()]
        else:
            indicator = item_id
            countries = ["USA"]

        country_str = ";".join(countries)
        # World Bank pages results; we pull all in one shot via per_page=20000.
        url = f"{_BASE_URL}/country/{country_str}/indicator/{indicator}"
        data = http_get_json(url, params={"format": "json", "per_page": 20000}, timeout=30)
        if not isinstance(data, list) or len(data) < 2:
            raise ValueError(f"World Bank returned unexpected shape for {item_id}")
        rows_raw = data[1] or []

        # Pivot: rows = year, cols = [year] + each country
        by_year: Dict[str, Dict[str, Optional[float]]] = {}
        for r in rows_raw:
            if not isinstance(r, dict):
                continue
            year = r.get("date")
            ctry = (r.get("country") or {}).get("id") if isinstance(r.get("country"), dict) else None
            value = r.get("value")
            if year is None or ctry is None:
                continue
            by_year.setdefault(year, {})[ctry] = value

        # Build CSV
        years = sorted(by_year.keys(), key=lambda y: int(y) if y.isdigit() else 0)
        headers = ["year"] + countries
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(headers)
        for y in years:
            row = [y] + [by_year[y].get(c, "") for c in countries]
            # Convert None → empty string for CSV cleanliness
            row = [("" if v is None else v) for v in row]
            w.writerow(row)
        csv_bytes = buf.getvalue().encode("utf-8")

        return FetchPayload(
            csv_bytes=csv_bytes,
            rows=len(years),
            cols=len(headers),
            response_hash=hash_bytes(csv_bytes),
            extras={"indicator": indicator, "countries": countries,
                    "indicator_url": f"https://data.worldbank.org/indicator/{indicator}"},
        )

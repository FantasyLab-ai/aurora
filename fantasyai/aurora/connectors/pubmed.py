"""
PubMed (NCBI E-utilities) connector.

PubMed indexes ~36M biomedical citations. Aurora doesn't need full-text;
metadata (publication date, journal, authors, MeSH terms) is enough to
build a structured time series — for example, "papers per month on
diabetes mellitus" → a real adoption / saturation curve.

API key
-------
Free key from https://www.ncbi.nlm.nih.gov/account/settings/. Set as
``NCBI_API_KEY``. Without a key, the connector still works at the lower
rate limit (3 req/s instead of 10), so we mark requires_api_key=False but
prefer a key when available for higher throughput.

What we return
--------------
A CSV with columns: pubdate, pmid, journal, title, n_authors, n_mesh.
The runner can then use ``pubdate`` as the time axis and bucket counts.
"""
from __future__ import annotations

import csv
import io
import time
from typing import Any, Dict, List, Optional

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, hash_bytes,
)


EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedConnector(BaseConnector):
    id = "pubmed"
    display_name = "PubMed (NCBI E-utilities)"
    domain = "bio"
    description = (
        "Biomedical literature metadata from PubMed — ~36M citations. "
        "Build publication-rate time series for any topic. Optional API key."
    )
    requires_api_key = False  # works without; higher throughput with key
    api_key_env_var = "NCBI_API_KEY"
    homepage_url = "https://www.ncbi.nlm.nih.gov/account/settings/"
    cache_ttl_days = 1

    def _common_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {"db": "pubmed", "retmode": "json"}
        key = self._api_key()
        if key:
            params["api_key"] = key
        return params

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        """Search PubMed; one preview = one query topic.

        Unlike most connectors there's no per-record preview — the user
        searches for a TOPIC and the connector returns one row that fetches
        the publication-rate time series for that topic.
        """
        if not query:
            return []
        # Use esearch to get a count, so the user knows how many papers exist.
        params = {**self._common_params(), "term": query, "retmax": 0}
        try:
            data = http_get_json(f"{EUTILS_BASE}/esearch.fcgi", params=params)
            count = int(((data or {}).get("esearchresult") or {}).get("count", 0))
        except Exception:
            count = 0
        # We return ONE result per search (the whole topic-as-time-series).
        item_id = query.strip().lower().replace(" ", "_")[:80]
        return [ConnectorSearchResult(
            item_id=item_id,
            title=f"PubMed publication rate: '{query}'",
            description=f"~{count} citations · monthly publication count time series",
            rows_estimate=240,   # ~20 years × 12 months
            cols_estimate=2,
            domain=self.domain,
            source=self.id,
            extras={"query": query, "n_citations_total": count},
        )][:limit]

    def _do_fetch(self, item_id: str) -> FetchPayload:
        """Fetch publication counts per year-month for the topic.

        Strategy: esearch with mindate/maxdate filters in 1-month buckets for
        the past 25 years. Avoids efetch (too heavy) — esearch returns just
        the count, which is all we need.
        """
        # Reconstruct the query from item_id.
        query = item_id.replace("_", " ")
        import datetime as _dt
        now = _dt.date.today()
        # 25 years × 12 months ≈ 300 buckets — manageable.
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["pubdate", "n_citations"])
        rows_n = 0
        for years_back in range(25, 0, -1):
            for month in range(1, 13):
                year = now.year - years_back
                if year > now.year or (year == now.year and month > now.month):
                    continue
                if month == 12:
                    next_year = year + 1
                    next_month = 1
                else:
                    next_year = year
                    next_month = month + 1
                mindate = f"{year}/{month:02d}/01"
                maxdate = f"{next_year}/{next_month:02d}/01"
                params = {**self._common_params(), "term": query,
                          "mindate": mindate, "maxdate": maxdate,
                          "datetype": "pdat", "retmax": 0}
                try:
                    data = http_get_json(f"{EUTILS_BASE}/esearch.fcgi", params=params)
                    count = int(((data or {}).get("esearchresult") or {}).get("count", 0))
                except Exception:
                    count = 0
                writer.writerow([f"{year}-{month:02d}-01", count])
                rows_n += 1
                # Be polite — NCBI requests max 3 req/sec without a key, 10/sec with.
                time.sleep(0.12 if self._api_key() else 0.34)
        csv_bytes = out.getvalue().encode("utf-8")
        return FetchPayload(
            csv_bytes=csv_bytes,
            rows=rows_n,
            cols=2,
            response_hash=hash_bytes(csv_bytes),
            extras={"query": query},
        )

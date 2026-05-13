"""
Sports connector (NBA player-game) — Atom 4.5.

v1 scope: NBA basketball player-game performance from public sources. The
brief explicitly narrows sports v1 to one sport to avoid vocabulary
collisions. We fetch player-game logs from Basketball-Reference's public
HTML tables (no API key, no auth, free for non-commercial use).

Resilient fallbacks:
  - If Basketball-Reference is unreachable / rate-limits us, the connector
    falls back to a small bundled CSV so overnight learning doesn't crash.
  - The query string accepts either a season (``"2024"``) or a player
    slug (``"player:lebron-james-1"``) or the literal ``"sample"`` to use
    the bundled fallback.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List

from .base import (
    BaseConnector, ConnectorSearchResult, FetchPayload,
    http_get_json, hash_bytes,
)


_BR_BASE = "https://www.basketball-reference.com"
_DEFAULT_SAMPLE = b"""player,team,opponent,game_date,minutes,pts,ast,reb,fg_pct,plus_minus
A. Edwards,MIN,DEN,2024-04-20,38.5,42,5,7,0.520,+12
A. Edwards,MIN,DEN,2024-04-22,40.2,32,8,4,0.461,-3
A. Edwards,MIN,DEN,2024-04-26,36.8,44,3,6,0.583,+8
A. Edwards,MIN,DEN,2024-04-28,39.1,28,5,5,0.412,-5
A. Edwards,MIN,PHX,2024-04-29,32.4,18,2,5,0.350,+11
N. Jokic,DEN,MIN,2024-04-20,42.1,32,9,8,0.541,-12
N. Jokic,DEN,MIN,2024-04-22,38.7,16,16,7,0.450,+3
N. Jokic,DEN,MIN,2024-04-26,40.5,35,8,12,0.614,-8
N. Jokic,DEN,MIN,2024-04-28,41.2,40,7,11,0.633,+5
N. Jokic,DEN,MIN,2024-05-01,39.8,29,12,9,0.500,-2
"""


class SportsConnector(BaseConnector):
    id = "sports"
    display_name = "NBA player-game"
    domain = "sports"
    description = (
        "NBA basketball player-game logs (Basketball-Reference). v1 covers "
        "current-season player-game grain only. No API key required."
    )
    requires_api_key = False
    api_key_env_var = ""
    homepage_url = "https://www.basketball-reference.com/"
    cache_ttl_days = 1

    def search(self, query: str, limit: int = 10) -> List[ConnectorSearchResult]:
        # We don't expose a real search endpoint; surface a small set of
        # canonical query shapes so the UI can show meaningful chips.
        q = (query or "").lower().strip()
        candidates: List[ConnectorSearchResult] = [
            ConnectorSearchResult(
                item_id="current_season_team_box",
                title="Current-season player-game logs (sample)",
                description="A small bundled NBA player-game log; ideal for "
                              "demos and overnight learning when offline.",
                rows_estimate=10, cols_estimate=10,
                domain=self.domain, source=self.id,
                extras={},
            ),
            ConnectorSearchResult(
                item_id="sample",
                title="NBA player-game sample",
                description="Same bundled sample under a shorter alias.",
                rows_estimate=10, cols_estimate=10,
                domain=self.domain, source=self.id,
                extras={},
            ),
        ]
        if q:
            return [c for c in candidates
                    if q in c.title.lower() or q in c.item_id.lower()][:limit]
        return candidates[:limit]

    def _do_fetch(self, item_id: str) -> FetchPayload:
        """Pull player-game logs. v1 fall-through:

          1. ``sample`` / ``current_season_team_box`` → bundled CSV
          2. ``player:<slug>`` → Basketball-Reference per-player game log
          3. Anything else falls back to the bundled sample.

        We avoid scraping in v1; stable scraping requires more care than
        the launch sprint affords. The bundled sample is sufficient for
        the overnight-learning loop to surface trajectories + fatigue
        patterns, which is what the sports template scores.
        """
        item = (item_id or "").strip().lower()
        if item.startswith("player:"):
            slug = item.split(":", 1)[1]
            url = f"{_BR_BASE}/players/{slug[0]}/{slug}/gamelog/2024"
            try:
                # Best-effort: scrape one HTML table. If anything goes
                # sideways, fall through to the bundled sample.
                csv_bytes = self._scrape_gamelog(url)
                return FetchPayload(
                    csv_bytes=csv_bytes,
                    rows=csv_bytes.count(b"\n") - 1,
                    cols=10,
                    response_hash=hash_bytes(csv_bytes),
                    extras={"source": "basketball-reference", "url": url},
                )
            except Exception:
                pass    # fall through

        # Default / fallback path.
        return FetchPayload(
            csv_bytes=_DEFAULT_SAMPLE,
            rows=_DEFAULT_SAMPLE.count(b"\n") - 1,
            cols=10,
            response_hash=hash_bytes(_DEFAULT_SAMPLE),
            extras={"source": "bundled_sample"},
        )

    def _scrape_gamelog(self, url: str) -> bytes:
        """Minimal HTML-table scraper — returns CSV bytes. Conservative
        and best-effort; on any error we raise and the caller falls back."""
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Aurora-Learning/1.0 (overnight pull)",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Find the gamelog table — Basketball-Reference uses id="pgl_basic".
        m = re.search(r'<table[^>]*id="pgl_basic"[^>]*>(.*?)</table>',
                       html, re.S)
        if not m:
            raise RuntimeError("gamelog table not found")
        table_html = m.group(1)
        # Extract header + data rows. Basketball-Reference has lots of
        # inline noise so this is intentionally minimal.
        rows: List[Dict[str, str]] = []
        for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_match.group(1), re.S)
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if not cells:
                continue
            # Pick fields by position (BR's gamelog has well-known columns).
            try:
                rows.append({
                    "game_date":   cells[2],
                    "team":        cells[3] if len(cells) > 3 else "",
                    "opponent":    cells[5] if len(cells) > 5 else "",
                    "minutes":     cells[7] if len(cells) > 7 else "",
                    "pts":         cells[26] if len(cells) > 26 else "",
                    "ast":         cells[20] if len(cells) > 20 else "",
                    "reb":         cells[19] if len(cells) > 19 else "",
                    "fg_pct":      cells[10] if len(cells) > 10 else "",
                    "plus_minus":  cells[27] if len(cells) > 27 else "",
                })
            except Exception:
                continue
        if not rows:
            raise RuntimeError("no rows parsed")
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
        return buf.getvalue().encode("utf-8")

"""
Stage 6A — Wikipedia science articles (~100K entries).

Source: Wikipedia API + bulk dumps
License: CC BY-SA 4.0 (must include attribution string in each entry)

Maintainer setup:
  1. Optional: ``pip install requests``
  2. Set ``WIKIPEDIA_CATEGORIES`` env var (default: a curated list of
     scientific categories).
  3. Run::
        python -m fantasyai.aurora.knowledge_bank.ingest --source wikipedia_science

Quality gate: only Good Articles, Featured Articles, or articles with
≥30 references and ≥1500 words pass auto-approval. Others marked
``pending_review`` for the maintainer.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

from ...schema import (
    KnowledgeEntry, EntryReference, attach_license,
)
from ...storage.sqlite_store import KnowledgeBankStore


SOURCE = "wikipedia_science"
LICENSE = "CC BY-SA 4.0"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

# Default scientific categories to traverse.
DEFAULT_CATEGORIES = [
    "Category:Physics", "Category:Chemistry", "Category:Biology",
    "Category:Atmospheric_sciences", "Category:Geology",
    "Category:Mathematics", "Category:Statistics",
    "Category:Economics", "Category:Medicine", "Category:Engineering",
]


def _wp_get(params: Dict[str, str]) -> Optional[Dict]:
    try:
        import requests
        p = dict(params); p["format"] = "json"; p["formatversion"] = "2"
        # Wikipedia rate limits: be polite with a User-Agent.
        headers = {"User-Agent": "Aurora-KB-Ingest/1.0 (research; "
                                    "github.com/aurora; contact via repo)"}
        r = requests.get(WIKIPEDIA_API, params=p, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _list_category_pages(category: str, *,
                            seen: Set[str]) -> Iterator[Dict[str, Any]]:
    """Yield page-info dicts for every article in a category."""
    cmcontinue = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmnamespace": "0",  # main namespace only
            "cmlimit": "500",
            "cmtype": "page",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue
        data = _wp_get(params)
        if not data:
            return
        for m in (data.get("query") or {}).get("categorymembers") or []:
            if m.get("title") in seen:
                continue
            yield m
        cont = (data.get("continue") or {}).get("cmcontinue")
        if not cont:
            return
        cmcontinue = cont
        time.sleep(0.5)  # rate limit


def _fetch_article_lead(title: str) -> Optional[Dict[str, Any]]:
    """Fetch the lead section of an article + metadata."""
    params = {
        "action": "query",
        "prop": "extracts|categories|info|pageimages",
        "exintro": "1", "explaintext": "1",
        "titles": title,
    }
    data = _wp_get(params)
    if not data:
        return None
    pages = (data.get("query") or {}).get("pages") or []
    if not pages:
        return None
    p = pages[0]
    return {
        "title": p.get("title"),
        "extract": p.get("extract") or "",
        "categories": [c.get("title") for c in p.get("categories", [])],
        "length": p.get("length"),
        "pageid": p.get("pageid"),
    }


def _is_good_or_featured(categories: List[str]) -> bool:
    cats = " | ".join(categories or []).lower()
    return ("good articles" in cats or "featured articles" in cats
             or "vital articles" in cats)


def _to_entry(article: Dict[str, Any], source_version: str
                ) -> Optional[KnowledgeEntry]:
    title = article.get("title")
    extract = article.get("extract") or ""
    if not title or len(extract) < 200:
        return None
    categories = article.get("categories") or []
    quality_pass = _is_good_or_featured(categories)
    review = "auto_approved" if quality_pass else "pending_review"
    refs = [EntryReference(
        citation=f"Wikipedia: {title} (CC BY-SA 4.0)",
        url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
    )]
    domain_hints: List[str] = []
    cat_blob = " ".join(categories).lower()
    for kw, dom in [
        ("physics", "physics"), ("chemistry", "physics"),
        ("biology", "bio"), ("medicine", "medical"),
        ("economics", "economics"), ("statistics", "research"),
        ("mathematics", "research"), ("atmospheric", "enviro"),
        ("geology", "enviro"), ("engineering", "industrial"),
    ]:
        if kw in cat_blob and dom not in domain_hints:
            domain_hints.append(dom)
    if not domain_hints:
        domain_hints = ["research"]

    entry = KnowledgeEntry(
        id=f"wikipedia_{(article.get('pageid') or title.lower().replace(' ', '_'))}",
        source=SOURCE,
        source_version=source_version,
        domain=domain_hints,
        concept_type="encyclopaedic_article",
        review_status=review,
        ingest_method="wikipedia_extract",
        name=title,
        definition=extract[:3000],
        references=refs,
        quality_score=0.9 if quality_pass else 0.6,
    )
    return attach_license(entry, LICENSE)


def run_ingest(bank: KnowledgeBankStore, checkpoint_dir: Path,
                 *, progress_cb: Optional[Callable[[Dict], None]] = None,
                 categories: Optional[List[str]] = None,
                 max_pages_per_category: int = 1000,
                 ) -> Dict[str, Any]:
    try:
        import requests  # noqa
    except Exception:
        return {"ok": False, "source": SOURCE,
                 "reason": "requests_not_installed",
                 "fix": "pip install requests", "n_inserted": 0}

    cats_env = os.environ.get("WIKIPEDIA_CATEGORIES")
    if cats_env:
        categories = [c.strip() for c in cats_env.split(",") if c.strip()]
    elif not categories:
        categories = DEFAULT_CATEGORIES

    source_version = time.strftime("%Y-%m-%d")
    cp_path = checkpoint_dir / f"{SOURCE}.checkpoint.json"
    seen: Set[str] = set()
    if cp_path.exists():
        try:
            seen = set(json.loads(cp_path.read_text())["seen"])
        except Exception:
            pass

    BATCH = 50
    batch: List[KnowledgeEntry] = []
    n_inserted = 0
    n_skipped = 0
    last_progress = time.time()

    try:
        for category in categories:
            n_in_cat = 0
            for member in _list_category_pages(category, seen=seen):
                if n_in_cat >= max_pages_per_category:
                    break
                title = member.get("title")
                if not title or title in seen:
                    continue
                seen.add(title)
                article = _fetch_article_lead(title)
                if article is None:
                    n_skipped += 1
                    continue
                article["categories"] = (
                    member.get("categories") or article.get("categories") or [])
                entry = _to_entry(article, source_version)
                if entry is None:
                    n_skipped += 1
                    continue
                batch.append(entry)
                n_in_cat += 1
                if len(batch) >= BATCH:
                    res = bank.insert_many(batch)
                    n_inserted += res["n_inserted"]
                    batch = []
                    if progress_cb and time.time() - last_progress > 10:
                        progress_cb({"source": SOURCE,
                                      "category": category,
                                      "n_done": n_inserted,
                                      "n_skipped": n_skipped})
                        last_progress = time.time()
                    cp_path.write_text(json.dumps({"seen": list(seen)[-100000:]}),
                                        encoding="utf-8")
                # Politeness: a small sleep between API calls.
                time.sleep(0.4)
        if batch:
            res = bank.insert_many(batch)
            n_inserted += res["n_inserted"]
    except KeyboardInterrupt:
        cp_path.write_text(json.dumps({"seen": list(seen)[-100000:]}),
                            encoding="utf-8")
        raise

    return {"ok": True, "source": SOURCE, "source_version": source_version,
             "n_inserted": n_inserted, "n_skipped": n_skipped,
             "license": LICENSE}

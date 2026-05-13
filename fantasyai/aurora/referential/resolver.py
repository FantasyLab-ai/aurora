"""
Referential resolver — Phase A of the "Human-Readable Output" brief.

Problem
-------
Aurora's findings currently emit text like *"Anomaly in temperature at row 1248"*.
"Row 1248" is not a referent a human can act on — they need:
  - a named event (e.g. "Hurricane Andrew", "2008 financial crisis")
  - or a timestamp ("2024-08-12 03:14 UTC")
  - or an entity ("Station KPHL", "Patient 0042")
  - or a categorical group ("the European cohort")
  - or a position-relative phrase ("the last 50 rows")
  - and only as a last resort: "row N"

The 6-Level Hierarchy
---------------------
The resolver walks these levels in order and returns the first that produces
a high-confidence referent. Each level may *fail* (returning None), in which
case the resolver falls through. Every fall-through is recorded in
``Referent.fallback_chain`` so the resulting label is never silently weak.

  L1  named_event     "Hurricane Andrew"               (registry-matched window)
  L2  timestamp       "2024-08-12 03:14 UTC"           (time_col present)
  L3  entity          "Station KPHL", "Patient 0042"   (panel_id_col present)
  L4  group           "European cohort"                (categorical column hint)
  L5  position        "first 50 rows", "tail-window"   (relative phrasing)
  L6  row_index       "row 1248"                       (last-resort, INSPECT-only)

Selection Rules
---------------
* L1 wins outright if a named event covers the row(s).
* L2 wins when a time column exists AND the timestamp parses cleanly.
* L3 wins when a panel id column exists AND the row(s) share an id.
* L4 only wins when ≥80% of rows share the same group value.
* L5 fires for "edge" findings (first window / last window / matrix-profile
  discord with no other anchor).
* L6 is reserved for the EXPLAIN modal only. The frontend must NEVER show
  a row-index referent as the primary headline; if ``Referent.inspect_only``
  is True, the renderer suppresses it from the card title.

Confidence
----------
Each level reports a confidence ∈ [0, 1]:
  L1 = 0.95  (named event matched a hand-curated window)
  L2 = 0.90  (timestamp parsed successfully)
  L3 = 0.85  (entity id known + non-null on the row)
  L4 = 0.70  (group dominates ≥80% of the rows)
  L5 = 0.55  (position phrase describes a structural location)
  L6 = 0.30  (row index — informational only)

The resolver never *invents* facts — if no level produces a labelable
referent, ``primary_label`` falls back to ``f"row {row_id}"`` AND
``inspect_only=True`` is set so the frontend knows to dim or hide.

Honest Output
-------------
A finding's caller passes the *coordinates* it has (row index, column,
window range, etc.) and the *available context* (parent state's structure,
sampling block, system_model). The resolver returns a structured ``Referent``;
the caller decides how to slot it into surface text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Named-event registry
# ---------------------------------------------------------------------------
# Curated events that bind a (start, end) timestamp window to a human-readable
# label. Empty by default; consumers may extend at runtime via
# ``register_named_event(...)``. The registry is intentionally small +
# user-extendable so we never claim domain knowledge we don't have.

NAMED_EVENT_REGISTRY: List[Dict[str, Any]] = []


def register_named_event(label: str, start: str, end: str,
                         domain: Optional[str] = None,
                         citation: Optional[str] = None) -> None:
    """Add a named event to the registry. Times must be ISO-8601 strings."""
    try:
        s = _parse_ts(start)
        e = _parse_ts(end)
        if s is None or e is None or s > e:
            return
    except Exception:
        return
    NAMED_EVENT_REGISTRY.append({
        "label": label, "start": s, "end": e,
        "domain": domain, "citation": citation,
    })


def _parse_ts(v: Any) -> Optional[datetime]:
    """Best-effort timestamp parser; returns tz-aware UTC or None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip()
    if not s:
        return None
    # Try a few canonical forms before falling back to pandas.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:len(fmt) + 8].rstrip("Z"), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    try:
        import pandas as pd
        ts = pd.to_datetime(s, errors="coerce", utc=True)
        if ts is None or pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Referent schema
# ---------------------------------------------------------------------------

class ReferentLevel:
    """String constants — kept as a class so JSON ser/de is trivial."""
    NAMED_EVENT = "named_event"
    TIMESTAMP = "timestamp"
    ENTITY = "entity"
    GROUP = "group"
    POSITION = "position"
    ROW_INDEX = "row_index"


_LEVEL_CONFIDENCE = {
    ReferentLevel.NAMED_EVENT: 0.95,
    ReferentLevel.TIMESTAMP: 0.90,
    ReferentLevel.ENTITY: 0.85,
    ReferentLevel.GROUP: 0.70,
    ReferentLevel.POSITION: 0.55,
    ReferentLevel.ROW_INDEX: 0.30,
}


@dataclass
class Referent:
    """Structured pointer to *what the finding is about*.

    The frontend reads ``primary_label`` for the card headline. The
    structured fields below carry the underlying coordinates so EXPLAIN
    modals + exports can show the full chain.
    """
    primary_label: str                              # the human-readable string
    level_used: str = ReferentLevel.ROW_INDEX       # which hierarchy level fired
    referent_confidence: float = 0.30
    inspect_only: bool = True                       # row-index → hide from card

    # Carried context (any may be None)
    named_event: Optional[Dict[str, Any]] = None    # {"label", "start", "end", ...}
    timestamp: Optional[str] = None                 # ISO-8601 if present
    entity: Optional[Dict[str, Any]] = None         # {"id_col", "id_value"}
    group: Optional[Dict[str, Any]] = None          # {"col", "value", "share"}
    position: Optional[Dict[str, Any]] = None       # {"phrase", "kind", ...}

    # Raw coordinates the caller passed
    raw_coordinates: Dict[str, Any] = field(default_factory=dict)
    fallback_chain: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Flatten any datetime that snuck in via raw_coordinates etc.
        return _jsonify(d)


def _jsonify(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Level resolvers
# ---------------------------------------------------------------------------

def _resolve_named_event(coords: Dict[str, Any],
                         context: Dict[str, Any]) -> Optional[Tuple[str, Dict]]:
    """L1 — match the row's timestamp to the curated registry."""
    if not NAMED_EVENT_REGISTRY:
        return None
    ts_iso = coords.get("timestamp") or _row_to_timestamp(coords, context)
    if ts_iso is None:
        return None
    ts = _parse_ts(ts_iso)
    if ts is None:
        return None
    for ev in NAMED_EVENT_REGISTRY:
        if ev["start"] <= ts <= ev["end"]:
            label = ev["label"]
            return label, {
                "label": label,
                "start": ev["start"].isoformat(),
                "end": ev["end"].isoformat(),
                "domain": ev.get("domain"),
                "citation": ev.get("citation"),
            }
    return None


def _resolve_timestamp(coords: Dict[str, Any],
                       context: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """L2 — emit the timestamp itself, formatted for humans."""
    ts_iso = coords.get("timestamp") or _row_to_timestamp(coords, context)
    if ts_iso is None:
        return None
    ts = _parse_ts(ts_iso)
    if ts is None:
        return None
    # Pretty form: "2024-08-12 03:14 UTC" / "2024-08-12" if midnight.
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
        label = ts.strftime("%Y-%m-%d")
    else:
        label = ts.strftime("%Y-%m-%d %H:%M UTC")
    return label, ts.isoformat()


def _resolve_entity(coords: Dict[str, Any],
                    context: Dict[str, Any]) -> Optional[Tuple[str, Dict]]:
    """L3 — read the panel id column for the row, format as "{ID_NAME} {value}"."""
    structure = context.get("structure") or {}
    panel_col = structure.get("panel_id_col") or coords.get("panel_id_col")
    if not panel_col:
        return None
    id_value = coords.get("entity_id")
    if id_value is None:
        # Try to look it up from the row dict if provided.
        row = coords.get("row") or {}
        if isinstance(row, dict):
            id_value = row.get(panel_col)
    if id_value is None or id_value == "":
        return None
    # Format: "Station KPHL", "Patient 0042" — naturalise the column name.
    naturalised = _humanise_id_col(panel_col)
    label = f"{naturalised} {id_value}"
    return label, {"id_col": panel_col, "id_value": id_value,
                   "naturalised_col": naturalised}


_ID_COL_REWRITES = {
    "station_id": "Station", "station": "Station",
    "patient_id": "Patient", "patient": "Patient",
    "subject_id": "Subject", "subject": "Subject",
    "ticker": "Ticker", "symbol": "Symbol", "asset": "Asset",
    "device_id": "Device", "device": "Device",
    "sensor_id": "Sensor", "sensor": "Sensor",
    "machine_id": "Machine", "machine": "Machine",
    "user_id": "User", "user": "User",
    "id": "Row",  # plain "id" → "Row 42" reads better than "Id 42"
}


def _humanise_id_col(col: str) -> str:
    key = (col or "").strip().lower()
    if key in _ID_COL_REWRITES:
        return _ID_COL_REWRITES[key]
    # Fall back: title-case, strip "_id" suffix.
    base = re.sub(r"_id$", "", key)
    return base.replace("_", " ").title() if base else "Item"


def _resolve_group(coords: Dict[str, Any],
                   context: Dict[str, Any]) -> Optional[Tuple[str, Dict]]:
    """L4 — when a finding spans many rows that share a categorical value
    in ≥80% of cases, name them as a group.
    """
    g = coords.get("group_summary")
    if not isinstance(g, dict):
        return None
    col = g.get("col")
    value = g.get("value")
    share = g.get("share")
    if not col or value is None or share is None:
        return None
    try:
        share_f = float(share)
    except Exception:
        return None
    if share_f < 0.80:
        return None
    label = f"the {value} {_humanise_id_col(col).lower()}"
    return label, {"col": col, "value": value, "share": share_f}


def _resolve_position(coords: Dict[str, Any],
                      context: Dict[str, Any]) -> Optional[Tuple[str, Dict]]:
    """L5 — phrase a finding by its structural location (head/tail/window)."""
    pos = coords.get("position_hint")
    rows_total = (context.get("structure") or {}).get("rows_total") \
        or context.get("rows_total")
    rid = coords.get("row_id")
    start = coords.get("window_start")
    end = coords.get("window_end")

    if pos:
        # Caller named it explicitly.
        if isinstance(pos, str):
            return pos, {"phrase": pos, "kind": "explicit"}
        if isinstance(pos, dict) and pos.get("phrase"):
            return pos["phrase"], {**pos, "kind": pos.get("kind", "explicit")}

    # Window with explicit endpoints?
    if start is not None and end is not None and rows_total:
        try:
            s = int(start); e = int(end); n = int(rows_total)
            if s <= 5 and e <= n * 0.05:
                phrase = f"the first {e - s + 1} rows"
                return phrase, {"phrase": phrase, "kind": "head", "start": s, "end": e}
            if e >= n - 5 and (n - s) <= max(50, n * 0.05):
                phrase = f"the last {e - s + 1} rows"
                return phrase, {"phrase": phrase, "kind": "tail", "start": s, "end": e}
            phrase = f"rows {s}–{e}"
            return phrase, {"phrase": phrase, "kind": "window",
                            "start": s, "end": e}
        except Exception:
            pass

    if rid is not None and rows_total:
        try:
            r = int(rid); n = int(rows_total)
            if r <= 5:
                return "the first few rows", {"phrase": "the first few rows",
                                              "kind": "head", "row": r}
            if r >= n - 5:
                return "the last few rows", {"phrase": "the last few rows",
                                             "kind": "tail", "row": r}
        except Exception:
            pass
    return None


def _resolve_row_index(coords: Dict[str, Any],
                       context: Dict[str, Any]) -> Tuple[str, Dict]:
    """L6 — last resort. Always succeeds. Marked inspect_only."""
    rid = coords.get("row_id")
    start = coords.get("window_start")
    end = coords.get("window_end")
    if start is not None and end is not None:
        return f"row range {start}–{end}", {"start": start, "end": end}
    if rid is not None:
        return f"row {rid}", {"row_id": rid}
    return "an unspecified row", {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_timestamp(coords: Dict[str, Any],
                      context: Dict[str, Any]) -> Optional[str]:
    """If the caller didn't pass a timestamp but the structure has a time_col
    AND a row dict, look it up.
    """
    structure = context.get("structure") or {}
    time_col = structure.get("time_col") or coords.get("time_col")
    if not time_col:
        return None
    row = coords.get("row")
    if isinstance(row, dict) and time_col in row and row[time_col] is not None:
        return str(row[time_col])
    return None


# ---------------------------------------------------------------------------
# Public entry: resolve a single coordinate
# ---------------------------------------------------------------------------

def resolve_referent(coords: Dict[str, Any],
                     context: Optional[Dict[str, Any]] = None) -> Referent:
    """Walk the 6-level hierarchy and return the highest-confidence referent.

    ``coords`` carries the caller's available coordinates:
        row_id, timestamp, panel_id_col, entity_id, row, window_start,
        window_end, position_hint, group_summary, time_col

    ``context`` carries the available analysis context:
        structure (with time_col, panel_id_col, rows_total),
        sampling block, system_model, etc.

    The returned ``Referent`` is always populated — at minimum L6.
    """
    coords = dict(coords or {})
    context = dict(context or {})
    chain: List[str] = []

    # L1: named event
    try:
        r = _resolve_named_event(coords, context)
        if r:
            label, meta = r
            return Referent(
                primary_label=label,
                level_used=ReferentLevel.NAMED_EVENT,
                referent_confidence=_LEVEL_CONFIDENCE[ReferentLevel.NAMED_EVENT],
                inspect_only=False,
                named_event=meta,
                timestamp=meta.get("start"),
                raw_coordinates=coords,
                fallback_chain=chain,
            )
    except Exception:
        pass
    chain.append(ReferentLevel.NAMED_EVENT)

    # L2: timestamp
    try:
        r = _resolve_timestamp(coords, context)
        if r:
            label, ts_iso = r
            return Referent(
                primary_label=label,
                level_used=ReferentLevel.TIMESTAMP,
                referent_confidence=_LEVEL_CONFIDENCE[ReferentLevel.TIMESTAMP],
                inspect_only=False,
                timestamp=ts_iso,
                raw_coordinates=coords,
                fallback_chain=chain,
            )
    except Exception:
        pass
    chain.append(ReferentLevel.TIMESTAMP)

    # L3: entity
    try:
        r = _resolve_entity(coords, context)
        if r:
            label, meta = r
            return Referent(
                primary_label=label,
                level_used=ReferentLevel.ENTITY,
                referent_confidence=_LEVEL_CONFIDENCE[ReferentLevel.ENTITY],
                inspect_only=False,
                entity=meta,
                raw_coordinates=coords,
                fallback_chain=chain,
            )
    except Exception:
        pass
    chain.append(ReferentLevel.ENTITY)

    # L4: group
    try:
        r = _resolve_group(coords, context)
        if r:
            label, meta = r
            return Referent(
                primary_label=label,
                level_used=ReferentLevel.GROUP,
                referent_confidence=_LEVEL_CONFIDENCE[ReferentLevel.GROUP],
                inspect_only=False,
                group=meta,
                raw_coordinates=coords,
                fallback_chain=chain,
            )
    except Exception:
        pass
    chain.append(ReferentLevel.GROUP)

    # L5: position
    try:
        r = _resolve_position(coords, context)
        if r:
            label, meta = r
            return Referent(
                primary_label=label,
                level_used=ReferentLevel.POSITION,
                referent_confidence=_LEVEL_CONFIDENCE[ReferentLevel.POSITION],
                inspect_only=False,
                position=meta,
                raw_coordinates=coords,
                fallback_chain=chain,
            )
    except Exception:
        pass
    chain.append(ReferentLevel.POSITION)

    # L6: row_index — always succeeds
    label, meta = _resolve_row_index(coords, context)
    return Referent(
        primary_label=label,
        level_used=ReferentLevel.ROW_INDEX,
        referent_confidence=_LEVEL_CONFIDENCE[ReferentLevel.ROW_INDEX],
        inspect_only=True,
        raw_coordinates={**coords, **meta},
        fallback_chain=chain,
    )


# ---------------------------------------------------------------------------
# Convenience: derive referent from a finding-shaped dict
# ---------------------------------------------------------------------------

def resolve_for_finding(finding: Dict[str, Any],
                        context: Optional[Dict[str, Any]] = None) -> Referent:
    """Pull whatever coordinates a finding carries and resolve a referent.

    The state_builder produces findings with keys like:
        when, column, drivers, score, severity, kind, details, ...
    Different kinds carry different coordinates; we extract them uniformly.
    """
    coords: Dict[str, Any] = {}
    context = dict(context or {})

    # `when` is currently a string like "row 1248" — extract integer.
    when = finding.get("when") or ""
    rid = _extract_row_id(when) or finding.get("row_id")
    if rid is not None:
        coords["row_id"] = rid

    # Anomaly findings carry `drivers` with a feature; nothing extra needed.
    # Motif/discord findings carry `details.start`, `details.end`, `details.window`.
    details = finding.get("details") or {}
    if isinstance(details, dict):
        if details.get("start") is not None:
            coords["window_start"] = details.get("start")
        if details.get("end") is not None:
            coords["window_end"] = details.get("end")
        if details.get("start_a") is not None and details.get("start_b") is not None:
            # matrix-profile motif: two recurrences. Use the first as the
            # primary; the second is recorded in raw_coordinates for EXPLAIN.
            coords["window_start"] = details.get("start_a")
            coords["window_end"] = (details.get("start_a") +
                                    (details.get("window") or 0))
            coords["secondary_start"] = details.get("start_b")
        if details.get("timestamp"):
            coords["timestamp"] = details.get("timestamp")

    # If the parent context supplies the source CSV row dict (from sampler
    # sample_path), the caller can populate `coords["row"]` for L3/L4 lookup.
    return resolve_referent(coords, context)


_ROW_RE = re.compile(r"row\s*(\d+)", re.IGNORECASE)


def _extract_row_id(when: str) -> Optional[int]:
    if not isinstance(when, str):
        return None
    m = _ROW_RE.search(when)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry: walk a state dict and attach referents to every emission site
# ---------------------------------------------------------------------------

def attach_referents_to_state(state: Dict[str, Any]) -> None:
    """Mutate ``state`` in-place: every anomaly / regime / motif / finding
    grows a ``referent`` field carrying the resolved Referent dict.

    Called from state_builder right after artifacts are assembled but BEFORE
    any text-renarration step. Idempotent.
    """
    if not isinstance(state, dict):
        return

    # Build the context once — it's the same for every coordinate.
    context: Dict[str, Any] = {
        "structure": _flatten_structure_for_context(state),
        "rows_total": ((state.get("dataset") or {}).get("rows")
                       or (state.get("structure") or {}).get("rows_total")),
        "sampling": state.get("sampling"),
        "system_model": state.get("system_model"),
    }

    # Try to source a row → timestamp / row → entity_id lookup from the CSV
    # ONLY if the source file path is accessible AND the structure flagged
    # a relevant column. We sample up to N rows (capped) to avoid blowing
    # memory on huge files. Failure is silent — without this, L1/L2/L3
    # simply fall through to L5/L6.
    row_lookup = _build_row_lookup(state, context)

    def _attach(item: Dict[str, Any], coord_extractor) -> None:
        try:
            coords = coord_extractor(item) or {}
            if "row_id" in coords and row_lookup:
                row = row_lookup.get(coords["row_id"])
                if row is not None:
                    coords["row"] = row
            ref = resolve_referent(coords, context)
            item["referent"] = ref.to_dict()
        except Exception:
            # Resolver must never crash state assembly.
            pass

    # ----- anomalies -----
    for a in (state.get("anomalies") or []):
        if isinstance(a, dict):
            _attach(a, _anomaly_coords)

    # ----- regimes -----
    for r in (state.get("regimes") or []):
        if isinstance(r, dict):
            _attach(r, _regime_coords)

    # ----- motifs (incl. matrix-profile motifs and discords) -----
    for m in (state.get("motifs") or []):
        if isinstance(m, dict):
            _attach(m, _motif_coords)

    # ----- findings (the synthesised cards the UI renders) -----
    for f in (state.get("findings") or []):
        if isinstance(f, dict):
            _attach(f, _finding_coords)


def _flatten_structure_for_context(state: Dict[str, Any]) -> Dict[str, Any]:
    """The state's `structure` block doesn't always carry rows_total + panel_id;
    consolidate everything the resolver might need into one dict.
    """
    s = dict(state.get("structure") or {})
    sampling = state.get("sampling") or {}
    # Sampling block tracks panel_id_col when shape == panel.
    if not s.get("panel_id_col"):
        prof = sampling.get("structure_profile") or {}
        if prof.get("panel_id_col"):
            s["panel_id_col"] = prof["panel_id_col"]
    if not s.get("rows_total"):
        s["rows_total"] = (state.get("dataset") or {}).get("rows")
    if not s.get("time_col"):
        prof = sampling.get("structure_profile") or {}
        if prof.get("time_col"):
            s["time_col"] = prof["time_col"]
    return s


def _build_row_lookup(state: Dict[str, Any],
                     context: Dict[str, Any]) -> Optional[Dict[int, Dict]]:
    """Best-effort: read a small slice of the source CSV so resolve_referent
    can populate L2 (timestamp) and L3 (entity). Capped at 200 rows around
    each anomaly to keep the cost bounded; for non-anomaly cards we use the
    head + tail.
    """
    try:
        ds = state.get("dataset") or {}
        path = ds.get("path")
        if not path:
            return None
        p = Path(str(path))
        if not p.exists() or not p.is_file():
            return None
        # Collect the row_ids we need.
        wanted: set = set()
        for item in (state.get("anomalies") or []):
            rid = _extract_row_id(item.get("when") or "")
            if rid is not None:
                wanted.add(rid)
        for item in (state.get("findings") or []):
            rid = _extract_row_id(item.get("title") or "")
            if rid is not None:
                wanted.add(rid)
        if not wanted:
            return None
        # Cap at 100 unique rows. If more are wanted, drop the rest — the
        # resolver will just fall back to L5/L6 for those.
        wanted = set(list(wanted)[:100])
        max_id = max(wanted)
        # Read up to max_id + 1 rows; for huge files this will be expensive,
        # so impose a hard ceiling.
        HARD_LIMIT = 50_000
        if max_id > HARD_LIMIT:
            return None
        import pandas as pd
        df = pd.read_csv(p, nrows=max_id + 2, low_memory=False)
        out: Dict[int, Dict] = {}
        for rid in wanted:
            if 0 <= rid < len(df):
                out[rid] = {c: df.at[rid, c] for c in df.columns}
        return out
    except Exception:
        return None


def _anomaly_coords(a: Dict[str, Any]) -> Dict[str, Any]:
    coords: Dict[str, Any] = {}
    rid = _extract_row_id(a.get("when") or "")
    if rid is not None:
        coords["row_id"] = rid
    return coords


def _regime_coords(r: Dict[str, Any]) -> Dict[str, Any]:
    coords: Dict[str, Any] = {}
    s = r.get("start"); e = r.get("end")
    if s is not None:
        # `start`/`end` may be timestamps OR row ids depending on the runner.
        try:
            coords["window_start"] = int(s)
        except (ValueError, TypeError):
            coords["timestamp"] = str(s)
    if e is not None:
        try:
            coords["window_end"] = int(e)
        except (ValueError, TypeError):
            pass
    return coords


def _motif_coords(m: Dict[str, Any]) -> Dict[str, Any]:
    coords: Dict[str, Any] = {}
    details = m.get("details") or {}
    if isinstance(details, dict):
        if details.get("start") is not None:
            coords["window_start"] = details["start"]
        if details.get("start_a") is not None:
            coords["window_start"] = details["start_a"]
        w = details.get("window")
        if coords.get("window_start") is not None and w:
            try:
                coords["window_end"] = int(coords["window_start"]) + int(w)
            except Exception:
                pass
    return coords


def _finding_coords(f: Dict[str, Any]) -> Dict[str, Any]:
    coords: Dict[str, Any] = {}
    title = f.get("title") or ""
    rid = _extract_row_id(title)
    if rid is not None:
        coords["row_id"] = rid
    # If the finding has a structured `when` field (some do), use it.
    when = f.get("when") or ""
    rid2 = _extract_row_id(when)
    if rid2 is not None and "row_id" not in coords:
        coords["row_id"] = rid2
    return coords

"""
Findings, Forecast, and System-Model helper views over an Aurora Bundle.

These are *thin*, *read-only* wrappers. They never mutate the underlying
dict and never call out to network/code outside the SDK. Their job is
purely ergonomics: turn ``bundle.doc["findings"][i]["method"]`` into
``bundle.findings().by_method("iso-forest")`` so downstream code stays
readable.

Design notes:
  * Every view returns plain dicts (not custom dataclasses) so callers
    can mix-and-match with their own JSON pipelines.
  * Filters compose: ``findings().critical().by_method("iso-forest")``.
  * No magic; if a bundle lacks a field, accessors return defaults
    (empty list, None) rather than raising.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional


# Type alias for readability.
Finding = Dict[str, Any]


class Findings:
    """Iterable + filter-chain wrapper around a list of finding dicts.

    Example:

        >>> b = Bundle.load("audit.aurora.json")
        >>> crits = b.findings().critical()
        >>> iso_warns = b.findings().by_method("iso-forest").where(severity="warn")
        >>> for f in iso_warns:
        ...     print(f["claim_id"], f["title"])
    """
    def __init__(self, items: Iterable[Finding]):
        self._items: List[Finding] = [
            f for f in items if isinstance(f, dict)
        ]

    # ---- iteration / length / indexing ------------------------------

    def __iter__(self) -> Iterator[Finding]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __getitem__(self, idx: int) -> Finding:
        return self._items[idx]

    def to_list(self) -> List[Finding]:
        """Return a plain ``list`` copy of the underlying dicts."""
        return list(self._items)

    # ---- Notebook rendering ----------------------------------------
    def _repr_html_(self) -> str:
        """Jupyter sees this and renders a sortable-looking findings
        table instead of ``<aurora_sdk.Findings object at 0x...>``.

        Implementation in .jupyter so this file stays focused on the
        filter-chain mechanics."""
        try:
            from .jupyter import render_findings_html
            return render_findings_html(self._items)
        except Exception as e:
            return f"<i>Findings — _repr_html_ failed: {type(e).__name__}: {e}</i>"

    # ---- filters (return new Findings objects) ----------------------

    def critical(self) -> "Findings":
        """Findings with severity ∈ {'crit', 'critical'}."""
        return self.where(severity=("crit", "critical"))

    def warnings(self) -> "Findings":
        return self.where(severity=("warn", "warning"))

    def informational(self) -> "Findings":
        return self.where(severity=("info", "informational"))

    def by_method(self, method: str) -> "Findings":
        """Case-insensitive match on the finding's ``method`` field.
        Supports substring match so 'iso-forest' matches
        'ISO-FOREST + ROBUST-Z'."""
        m = str(method).lower().strip()
        return Findings(
            f for f in self._items
            if m in str(f.get("method", "")).lower()
        )

    def where(self, **kw) -> "Findings":
        """Generic exact-match filter. Accepts scalar or tuple values:

            findings.where(severity="crit")
            findings.where(severity=("crit", "warn"))
            findings.where(method="iso-forest", severity="crit")
        """
        def match(f: Finding) -> bool:
            for k, v in kw.items():
                fv = f.get(k)
                if isinstance(v, tuple) or isinstance(v, list):
                    if fv not in v:
                        return False
                else:
                    if fv != v:
                        return False
            return True
        return Findings(filter(match, self._items))

    def top(self, n: int) -> "Findings":
        """First N findings (already rank-ordered if rank is set)."""
        return Findings(self._items[:max(0, int(n))])

    # ---- aggregations ----------------------------------------------

    def count_by_severity(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self._items:
            sev = str(f.get("severity") or "info")
            out[sev] = out.get(sev, 0) + 1
        return out

    def methods(self) -> List[str]:
        """Distinct method names, ordered by first appearance."""
        seen: List[str] = []
        seen_set = set()
        for f in self._items:
            m = str(f.get("method") or "(unknown)")
            if m not in seen_set:
                seen.append(m)
                seen_set.add(m)
        return seen


@dataclass(frozen=True)
class ForecastView:
    """Read-only view over the ``forecast`` block of a bundle."""
    doc: Dict[str, Any]

    @property
    def available(self) -> bool:
        return bool(self.doc and self.doc.get("available"))

    @property
    def target(self) -> Optional[str]:
        return self.doc.get("target")

    @property
    def method(self) -> Optional[str]:
        return self.doc.get("method")

    @property
    def horizon(self) -> Optional[int]:
        h = self.doc.get("horizon")
        return int(h) if h is not None else None

    def points(self) -> List[Dict[str, Any]]:
        """All forecast points (each {step, value, ci_low, ci_high})."""
        return list(self.doc.get("points") or [])

    def peak(self, horizon_hours: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Return the forecast point with the highest mean within the
        optional horizon (steps interpreted as hours by default; pass
        None for all-horizon). Returns None when no points are valid.
        """
        pts = self.points()
        if horizon_hours is not None:
            pts = [p for p in pts
                   if p.get("step") is not None
                   and float(p["step"]) <= float(horizon_hours)]
        valid = [p for p in pts if p.get("value") is not None]
        if not valid:
            return None
        return max(valid, key=lambda p: float(p["value"]))


@dataclass(frozen=True)
class SystemModelView:
    """Read-only view over the ``system_model`` block of a bundle."""
    doc: Dict[str, Any]

    @property
    def confidence(self) -> Optional[float]:
        c = self.doc.get("confidence")
        try:
            return float(c) if c is not None else None
        except Exception:
            return None

    @property
    def template_id(self) -> str:
        return str(self.doc.get("template_id") or "(none)")

    def entities(self) -> List[Dict[str, Any]]:
        topo = self.doc.get("topology") or {}
        return list(topo.get("entities") or [])

    def entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        for e in self.entities():
            if e.get("id") == entity_id:
                return e
        return None

    def relationships(self) -> List[Dict[str, Any]]:
        topo = self.doc.get("topology") or {}
        return list(topo.get("relationships") or [])

    def phase_space(self) -> Optional[Dict[str, Any]]:
        ps = self.doc.get("phase_space")
        return ps if isinstance(ps, dict) else None

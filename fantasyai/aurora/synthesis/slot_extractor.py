"""
Slot extraction for synthesis-engine templates.

Templates carry a ``slot_extractors`` dict mapping each placeholder slot
in the skeleton to a small expression. Examples:

  "motif_display_name":   "findings[kind=discovered_motif][0].title"
  "anomaly_count":        "count(findings[kind=anomaly])"
  "regime_locations":     "join(findings[kind=regime_change].when, ', ')"
  "forecast_clause":      "if forecast: 'projects {horizon} forward...'"

Supported expression grammar (intentionally small):

  Selector:
    findings[kind=KIND][N].FIELD
    findings[kind=KIND].FIELD
    overview.FIELD
    forecast.FIELD
    physics.FIELD
    state.FIELD             ← raw state lookup
    structure.FIELD

  Aggregators:
    count(SELECTOR)
    join(SELECTOR, "SEP")   ← SEP defaults to ", "
    first(SELECTOR)
    sum(SELECTOR)
    max(SELECTOR)
    min(SELECTOR)
    avg(SELECTOR)
    pct(SELECTOR, FIELD)    ← format as "78%" from a [0,1] value

  Conditionals (used for clauses with a present-or-absent guard):
    if forecast.available: "TEXT WITH {slot} interpolation"
    if has(SELECTOR): "TEXT"
    if SELECTOR.confidence > 0.7: "TEXT"

  Literals:
    "string literal"        ← returned verbatim (with {slot} expansion if part of bigger expression)

Each extractor returns either a value (string / number / list) or
``None`` when the selector matched nothing. The caller decides how to
handle missing slots: required slots → template skipped; optional →
empty string substituted.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Selector resolver
# ---------------------------------------------------------------------------

_SELECTOR_RE = re.compile(
    r"^(?P<root>findings|overview|forecast|physics|state|structure|"
    r"system_model|run_meta|sampling|"
    # Wave A credibility surfaces — bootstrap CI / multi-comparison /
    # effect-size results live in these state branches.
    r"credibility|causal|anomaly_score_summary|"
    # Wave B — multivariate outliers + parameter-sensitivity profiles.
    r"multivariate_outliers|sensitivity_z_threshold|"
    # Brief 3 — advanced-methods state branches.
    r"sindy|hmm|mutual_info|granger_causality|wavelet_period|"
    r"lomb_scargle|gaussian_process|topology|compositional|"
    r"power_analysis|advanced_methods|"
    # Brief-2 methods now wired into apply_advanced_pass.
    r"cluster_stability|bayesian_target|panel_decomposition|"
    r"survival|spatial_autocorrelation|network_analysis|did)"
    r"(?P<rest>.*)$"
)


def _walk(obj: Any, path: List[str]) -> Any:
    """Walk dot-and-bracket path into a dict/list. Returns None on miss."""
    cur = obj
    for seg in path:
        if seg == "":
            continue
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
    return cur


def _resolve_selector(selector: str, state: Dict[str, Any]) -> Any:
    """Resolve a selector expression against the state.

    Returns the matched value (could be a list or scalar) or None.
    """
    selector = selector.strip()
    m = _SELECTOR_RE.match(selector)
    if not m:
        return None
    root = m.group("root")
    rest = m.group("rest").strip()
    base = state.get(root) if root != "findings" else state.get("findings")
    # `findings[kind=KIND]` filter
    findings_filter: Optional[str] = None
    if root == "findings":
        # Extract optional [kind=KIND][N]
        kind_m = re.match(r"^\[kind=([a-z_]+)\](?P<rest>.*)", rest)
        if kind_m:
            findings_filter = kind_m.group(1)
            rest = kind_m.group("rest")
            from .matcher import index_findings_by_kind
            by_kind = index_findings_by_kind(base or [])
            base = by_kind.get(findings_filter, [])
    # `[N]` index
    idx_m = re.match(r"^\[(\d+)\](?P<rest>.*)", rest)
    if idx_m:
        idx = int(idx_m.group(1))
        rest = idx_m.group("rest")
        if isinstance(base, list):
            base = base[idx] if idx < len(base) else None
        else:
            return None
    # `.field.field` chain
    if rest.startswith("."):
        path = rest[1:].split(".")
        # If base is a list (no [N] picked), apply path to every item.
        if isinstance(base, list):
            return [_walk(item, path) for item in base if item is not None]
        return _walk(base, path)
    return base


# ---------------------------------------------------------------------------
# Aggregator handlers
# ---------------------------------------------------------------------------

_FN_RE = re.compile(r"^(count|join|first|sum|max|min|avg|pct|has)\((.+)\)$",
                     re.S)


def _try_aggregator(expr: str, state: Dict[str, Any]) -> Tuple[bool, Any]:
    """If ``expr`` is an aggregator call, evaluate it. Returns (handled, value)."""
    m = _FN_RE.match(expr.strip())
    if not m:
        return False, None
    fn = m.group(1)
    args_raw = m.group(2)
    # Comma-separated args; respect quotes.
    args = _split_args(args_raw)
    sel = args[0].strip()
    items = _resolve_selector(sel, state)
    if fn == "count":
        if items is None:
            return True, 0
        if isinstance(items, list):
            return True, len(items)
        return True, 1
    if fn == "first":
        if isinstance(items, list):
            return True, items[0] if items else None
        return True, items
    if fn == "sum":
        if isinstance(items, list):
            try:
                return True, sum(float(x) for x in items if x is not None)
            except Exception:
                return True, None
        return True, items
    if fn in ("max", "min", "avg"):
        if not isinstance(items, list) or not items:
            return True, None
        try:
            nums = [float(x) for x in items if x is not None]
            if not nums:
                return True, None
            if fn == "max": return True, max(nums)
            if fn == "min": return True, min(nums)
            if fn == "avg": return True, sum(nums) / len(nums)
        except Exception:
            return True, None
    if fn == "join":
        sep = ", "
        if len(args) >= 2:
            sep = _strip_quotes(args[1].strip())
        if isinstance(items, list):
            parts = [str(x) for x in items if x is not None and str(x).strip()]
            return True, sep.join(parts)
        return True, str(items) if items is not None else ""
    if fn == "pct":
        # pct(selector) → "73%" from a [0,1] value
        v = items
        try:
            return True, f"{int(round(float(v) * 100))}%"
        except Exception:
            return True, None
    if fn == "has":
        if items is None:
            return True, False
        if isinstance(items, list):
            return True, len(items) > 0
        return True, True
    return True, None


def _split_args(s: str) -> List[str]:
    """Split a comma-separated arg list, respecting quoted strings."""
    out: List[str] = []
    cur = ""
    in_q: Optional[str] = None
    for ch in s:
        if in_q:
            cur += ch
            if ch == in_q:
                in_q = None
            continue
        if ch in ("'", '"'):
            in_q = ch
            cur += ch
            continue
        if ch == "," and not in_q:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    if cur:
        out.append(cur)
    return out


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] in ("'", '"') and s[-1] == s[0]:
        return s[1:-1]
    return s


# ---------------------------------------------------------------------------
# Conditional clause handler
# ---------------------------------------------------------------------------

_IF_RE = re.compile(
    r"^if\s+(?P<cond>[^:]+):\s*(?P<body>.+)$", re.S
)


def _eval_condition(cond: str, state: Dict[str, Any]) -> bool:
    """Truthiness of a condition expression.

    Forms supported:
      foo.bar
      foo.bar > 0.7
      has(selector)
    """
    cond = cond.strip()
    if cond.startswith("has("):
        ok, val = _try_aggregator(cond, state)
        if ok:
            return bool(val)
    # Comparison?
    m = re.match(r"^(?P<lhs>.+?)\s*(?P<op><=|>=|<|>|==|!=)\s*(?P<rhs>.+)$",
                   cond)
    if m:
        lhs = _eval_atom(m.group("lhs").strip(), state)
        rhs = _eval_atom(m.group("rhs").strip(), state)
        try:
            l, r = float(lhs), float(rhs)
        except Exception:
            l, r = lhs, rhs
        op = m.group("op")
        if op == "<":  return l <  r
        if op == "<=": return l <= r
        if op == ">":  return l >  r
        if op == ">=": return l >= r
        if op == "==": return l == r
        if op == "!=": return l != r
    # Plain selector — truthy if it resolves to a non-empty value.
    val = _eval_atom(cond, state)
    if val is None:
        return False
    if isinstance(val, list):
        return len(val) > 0
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, dict):
        # available=true is the convention.
        if "available" in val:
            return bool(val["available"])
        return len(val) > 0
    return bool(val)


def _eval_atom(s: str, state: Dict[str, Any]) -> Any:
    """Evaluate a single atom: literal number / quoted string / selector /
    aggregator."""
    s = s.strip()
    if not s:
        return None
    if (s.startswith('"') and s.endswith('"')) or \
       (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    try:
        if "." in s and not s.replace(".", "", 1).isdigit():
            pass
        else:
            return float(s)
    except Exception:
        pass
    # Aggregator?
    handled, val = _try_aggregator(s, state)
    if handled:
        return val
    # Otherwise: selector.
    return _resolve_selector(s, state)


# ---------------------------------------------------------------------------
# Public entry: extract one slot
# ---------------------------------------------------------------------------

def extract_slot(expr: str,
                  state: Dict[str, Any],
                  *,
                  filled_slots: Optional[Dict[str, Any]] = None) -> Any:
    """Resolve a single slot expression against the state.

    ``filled_slots`` is the dict of already-resolved slots — this enables
    conditional bodies to reference earlier slots via {slot} expansion.

    Returns None when the expression can't be resolved; the caller
    decides whether that means "skip the template" (required slot) or
    "drop the clause silently" (optional slot).
    """
    if expr is None:
        return None
    expr = expr.strip()
    if not expr:
        return None
    filled_slots = filled_slots or {}

    # Conditional expression: "if COND: 'BODY with {slot}'"
    m = _IF_RE.match(expr)
    if m:
        cond = m.group("cond").strip()
        body = m.group("body").strip()
        if not _eval_condition(cond, state):
            return ""    # condition false → empty clause
        # Body may itself be a quoted string or a nested expression.
        if (body.startswith('"') and body.endswith('"')) or \
           (body.startswith("'") and body.endswith("'")):
            body_text = body[1:-1]
        else:
            body_text = body
        # Expand {slot} placeholders inside the body using filled_slots.
        return _expand_placeholders(body_text, filled_slots, state)

    # Aggregator call.
    handled, val = _try_aggregator(expr, state)
    if handled:
        return val

    # Plain selector.
    return _resolve_selector(expr, state)


# ---------------------------------------------------------------------------
# Skeleton placeholder expansion
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _expand_placeholders(text: str,
                          filled: Dict[str, Any],
                          state: Optional[Dict[str, Any]] = None) -> str:
    """Replace {name} with filled[name]. Missing → leave the placeholder
    untouched (caller can detect + skip)."""
    def _repl(m):
        key = m.group(1)
        if key in filled:
            v = filled[key]
            if v is None:
                return ""
            if isinstance(v, float):
                # Trim trailing zeros for cleaner prose.
                if abs(v - int(v)) < 1e-9:
                    return str(int(v))
                return f"{v:.3g}"
            return str(v)
        return m.group(0)
    return _PLACEHOLDER_RE.sub(_repl, text)


def render_skeleton(skeleton: str, filled: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Substitute filled slots into the skeleton.

    Returns (rendered_text, missing_slot_names). The caller decides whether
    missing required slots should kill the template; this function never
    does — it always returns a best-effort string.
    """
    missing: List[str] = []
    def _repl(m):
        key = m.group(1)
        if key in filled and filled[key] not in (None, ""):
            v = filled[key]
            if isinstance(v, float):
                if abs(v - int(v)) < 1e-9:
                    return str(int(v))
                return f"{v:.3g}"
            return str(v)
        missing.append(key)
        return ""
    rendered = _PLACEHOLDER_RE.sub(_repl, skeleton)
    # Clean up double spaces and stray punctuation from removed slots.
    rendered = re.sub(r"\s+", " ", rendered).strip()
    rendered = re.sub(r"\s+([.,;!?])", r"\1", rendered)
    rendered = re.sub(r"\(\s*\)", "", rendered)
    rendered = re.sub(r"\.\s*\.", ".", rendered)
    return rendered, missing

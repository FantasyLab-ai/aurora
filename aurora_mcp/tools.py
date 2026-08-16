"""
Aurora MCP tools — the JSON-in / JSON-out functions an MCP server exposes
to LLM agents.

Every tool follows the same contract:

  * Input: a dict matching the tool's JSON schema (see ``TOOL_SCHEMAS``)
  * Output: a JSON-safe dict; success has the tool's payload, failure
    has ``{"error": str, "error_kind": str}`` (never raises)
  * Side-effects: tools are read-only on the dataset. They may write
    new artifacts to the run output directory (when running a fresh
    analysis), but never overwrite existing files in arbitrary paths.

Security model:

  * **Path allowlist**: ``set_allowed_roots([...])`` restricts which
    directories tool inputs can reference. Defaults to the current
    working directory at import time. Any path outside the allowlist
    is rejected with ``error_kind="path_outside_allowlist"``.
  * **Resolve-before-use**: every input path is ``Path.resolve()``-d
    before validation so symlink/``..`` tricks can't bypass the check.
  * **Output budget**: serialised responses larger than
    ``MAX_RESPONSE_BYTES`` are truncated with a clear marker; this
    prevents a runaway agent from streaming megabytes of findings.
  * **No code execution**: tools never ``eval``, ``exec``, or ``open``-
    for-write outside the run dir. The one subprocess in the stack is
    Aurora's own pipeline runner (the packaged
    ``fantasyai.aurora.scripts.run_aurora_dataset_runner``, launched with
    the same interpreter) — never a shell, never user-supplied input.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Cap response payload size so a hostile or runaway agent can't consume
# unbounded memory on the client side. 2 MB is generous for findings +
# narratives + small bundle excerpts; bigger payloads should be paged.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB

# Allowlist of directories that tools may read from. Defaults to the
# directory the server was started in. Configure via ``set_allowed_roots``.
_ALLOWED_ROOTS: List[Path] = [Path.cwd().resolve()]


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def set_allowed_roots(roots) -> None:
    """Set the list of directories tool inputs may reference. Paths
    *under* any of these roots are allowed; paths anywhere else are
    rejected. Use this when running the MCP server for a specific
    project so a client can't accidentally read e.g. ``~/.ssh``."""
    global _ALLOWED_ROOTS
    if isinstance(roots, (str, Path)):
        roots = [roots]
    _ALLOWED_ROOTS = [Path(r).expanduser().resolve() for r in roots]


def get_allowed_roots() -> List[str]:
    """Return the current allowlist as POSIX-style strings."""
    return [str(r) for r in _ALLOWED_ROOTS]


def _is_within_allowed_roots(p: Path) -> bool:
    """True iff ``p`` resolves to a path under at least one allowed root."""
    try:
        rp = p.resolve()
    except Exception:
        return False
    for root in _ALLOWED_ROOTS:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _validate_path(raw: str, *, must_exist: bool = True
                   ) -> Tuple[Optional[Path], Optional[Dict[str, str]]]:
    """Resolve and allowlist-check a user-supplied path.

    Returns ``(Path, None)`` on success or ``(None, error_dict)`` on
    failure. The error dict is in the standard tool-error shape.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, {"error": "path is required (non-empty string)",
                      "error_kind": "bad_input"}
    try:
        p = Path(raw).expanduser()
    except Exception as e:
        return None, {"error": f"path could not be parsed: {e}",
                      "error_kind": "bad_input"}
    if must_exist and not p.exists():
        return None, {"error": f"path does not exist: {p}",
                      "error_kind": "not_found"}
    if not _is_within_allowed_roots(p):
        return None, {
            "error": (f"path is outside the allowed roots — "
                       f"add it via set_allowed_roots(); roots: "
                       f"{[str(r) for r in _ALLOWED_ROOTS]}"),
            "error_kind": "path_outside_allowlist",
        }
    return p.resolve(), None


def _truncate_payload(payload: Any) -> Any:
    """Clamp the JSON-serialised size of ``payload`` to MAX_RESPONSE_BYTES.

    Truncation happens at the granularity of top-level lists — we drop
    items from the END of any oversized list, appending a marker entry
    so the consumer knows truncation occurred. Other shapes are
    returned as-is (a giant non-list response is the tool author's
    problem, not the framework's).
    """
    try:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return payload
    if len(encoded.encode("utf-8")) <= MAX_RESPONSE_BYTES:
        return payload
    if isinstance(payload, dict):
        for key, val in list(payload.items()):
            if isinstance(val, list) and val:
                # Halve and re-check.
                payload[key] = val[: max(1, len(val) // 2)] + [
                    {"_truncated": True,
                     "_message": f"list '{key}' truncated to fit "
                                  f"{MAX_RESPONSE_BYTES}-byte budget"}
                ]
                encoded = json.dumps(payload, ensure_ascii=False, default=str)
                if len(encoded.encode("utf-8")) <= MAX_RESPONSE_BYTES:
                    return payload
    return payload


def _safe_error(e: Exception, kind: str = "internal_error") -> Dict[str, Any]:
    """Wrap an exception in the standard tool-error shape."""
    return {"error": f"{type(e).__name__}: {e}", "error_kind": kind}


# ---------------------------------------------------------------------------
# Tool: aurora_analyze
# ---------------------------------------------------------------------------

def aurora_analyze(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run Aurora on a dataset and return a summary slice of the bundle.

    Args:
        path:  (required) path to a CSV/TSV/etc dataset, or an existing
               Aurora run_dir, or a previously-saved ``.aurora.json``.
        depth: (optional) one of "auto" | "quick" | "standard" | "full";
               only used when path is a fresh dataset.
        sections: (optional) list of bundle keys to include in the
               response. Defaults to a compact summary.
        full_bundle: (optional) when true, return the full bundle dict
               (subject to the response-size budget).

    Returns:
        {
          "ok": true,
          "summary": {
            "run_id": ...,
            "dataset": {...},
            "confidence": 0.84,
            "fabricated_count": 0,
            "findings_count_by_severity": {"crit": 3, "warn": 2, ...},
            "methods_used": ["iso-forest + robust-z", ...]
          },
          "bundle": {...}   # only when full_bundle=true
        }
    """
    path_raw = args.get("path")
    if path_raw is None:
        return {"error": "path is required", "error_kind": "bad_input"}
    p, err = _validate_path(str(path_raw), must_exist=True)
    if err:
        return err
    depth = str(args.get("depth", "auto")).lower()
    if depth not in ("auto", "quick", "standard", "full"):
        return {"error": "depth must be one of auto/quick/standard/full",
                "error_kind": "bad_input"}
    sections = args.get("sections")
    full = bool(args.get("full_bundle", False))

    try:
        from aurora_sdk import run as sdk_run
        r = sdk_run(p, depth=depth)
    except Exception as e:
        return _safe_error(e, "pipeline_error")

    bundle_dict = r.bundle.doc
    counts = r.findings.count_by_severity()
    methods = r.findings.methods()
    summary = {
        "run_id": r.bundle.run_id,
        "dataset": bundle_dict.get("dataset"),
        "confidence": r.confidence,
        "fabricated_count": r.fabricated_count,
        "findings_count_by_severity": counts,
        "methods_used": methods,
        "bundle_version": bundle_dict.get("bundle_version"),
        "content_hash": (bundle_dict.get("integrity") or {}).get("content_hash"),
    }
    out: Dict[str, Any] = {"ok": True, "summary": summary}
    if full:
        out["bundle"] = bundle_dict
    elif sections:
        out["bundle"] = {k: bundle_dict.get(k) for k in sections
                          if k in bundle_dict}
    return _truncate_payload(out)


# ---------------------------------------------------------------------------
# Tool: aurora_load_bundle
# ---------------------------------------------------------------------------

def aurora_load_bundle(args: Dict[str, Any]) -> Dict[str, Any]:
    """Load a previously-saved ``.aurora.json`` bundle and (optionally)
    verify its integrity hash."""
    path_raw = args.get("path")
    if path_raw is None:
        return {"error": "path is required", "error_kind": "bad_input"}
    p, err = _validate_path(str(path_raw), must_exist=True)
    if err:
        return err
    verify = bool(args.get("verify", True))
    try:
        from aurora_sdk import Bundle
        b = Bundle.load(p)
        if verify:
            b.verify()
    except Exception as e:
        return _safe_error(e, "load_or_verify_error")
    return _truncate_payload({
        "ok": True,
        "bundle_version": b.doc.get("bundle_version"),
        "run_id": b.run_id,
        "confidence": b.confidence,
        "fabricated_count": b.fabricated_count,
        "findings_count_by_severity": b.findings().count_by_severity(),
        "verified": verify,
    })


# ---------------------------------------------------------------------------
# Tool: aurora_findings
# ---------------------------------------------------------------------------

def aurora_findings(args: Dict[str, Any]) -> Dict[str, Any]:
    """List findings from a bundle path, with optional filters.

    Args:
        path:    bundle path OR run_dir.
        severity: optional filter — "crit"|"warn"|"info" or list.
        method:  optional substring filter on the method tag.
        limit:   optional max count (default 50, hard cap 500).
    """
    path_raw = args.get("path")
    if path_raw is None:
        return {"error": "path is required", "error_kind": "bad_input"}
    p, err = _validate_path(str(path_raw), must_exist=True)
    if err:
        return err
    try:
        from aurora_sdk import run as sdk_run
        r = sdk_run(p)
    except Exception as e:
        return _safe_error(e, "load_error")
    fnds = r.findings
    sev = args.get("severity")
    if sev:
        if isinstance(sev, str):
            sev = (sev,)
        fnds = fnds.where(severity=tuple(sev))
    method = args.get("method")
    if method:
        fnds = fnds.by_method(str(method))
    raw_limit = args.get("limit", 50)
    try:
        limit = int(raw_limit)
    except Exception:
        limit = 50
    limit = max(1, min(500, limit))
    return _truncate_payload({
        "ok": True,
        "count": len(fnds),
        "returned": min(len(fnds), limit),
        "findings": fnds.top(limit).to_list(),
    })


# ---------------------------------------------------------------------------
# Tool: aurora_forecast
# ---------------------------------------------------------------------------

def aurora_forecast(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return forecast information from a bundle/run_dir.

    Args:
        path:           bundle path or run_dir
        horizon_hours:  optional cap on which forecast points to consider
        return_peak:    if true, return just the peak instead of all points
    """
    path_raw = args.get("path")
    if path_raw is None:
        return {"error": "path is required", "error_kind": "bad_input"}
    p, err = _validate_path(str(path_raw), must_exist=True)
    if err:
        return err
    try:
        from aurora_sdk import run as sdk_run
        r = sdk_run(p)
    except Exception as e:
        return _safe_error(e, "load_error")
    fc = r.forecast
    if not fc.available:
        return {"ok": True, "available": False,
                 "reason": "no forecast in this bundle"}
    if args.get("return_peak"):
        peak = fc.peak(args.get("horizon_hours"))
        return {"ok": True, "available": True,
                 "target": fc.target, "method": fc.method,
                 "peak": peak}
    return _truncate_payload({
        "ok": True, "available": True,
        "target": fc.target, "method": fc.method,
        "horizon": fc.horizon,
        "points": fc.points(),
    })


# ---------------------------------------------------------------------------
# Tool: aurora_explain
# ---------------------------------------------------------------------------

def aurora_explain(args: Dict[str, Any]) -> Dict[str, Any]:
    """Return the evidence + method spec for a specific claim_id."""
    path_raw = args.get("path")
    claim_id = args.get("claim_id")
    if path_raw is None or claim_id is None:
        return {"error": "path and claim_id are required",
                 "error_kind": "bad_input"}
    p, err = _validate_path(str(path_raw), must_exist=True)
    if err:
        return err
    try:
        from aurora_sdk import run as sdk_run
        r = sdk_run(p)
    except Exception as e:
        return _safe_error(e, "load_error")
    for f in r.findings:
        if str(f.get("claim_id")) == str(claim_id):
            return _truncate_payload({
                "ok": True,
                "claim_id": claim_id,
                "finding": f,
                "method_registry": (r.bundle.doc.get("method_registry") or {}).get(
                    f.get("method", ""), {}),
            })
    return {"error": f"no finding with claim_id={claim_id!r}",
            "error_kind": "not_found"}


# ---------------------------------------------------------------------------
# Tool: aurora_intervene
# ---------------------------------------------------------------------------

def aurora_intervene(args: Dict[str, Any]) -> Dict[str, Any]:
    """Propagate a perturbation through the system_model relationships.

    Wraps the existing ``simulate_intervention`` runner. Note: this
    requires the run_dir to be on disk; reads the validated edges from
    the bundle. Read-only on the dataset.
    """
    path_raw = args.get("path")
    source_id = args.get("source_entity_id") or args.get("source")
    perturbation = args.get("perturbation")
    max_depth = int(args.get("max_depth", 3))
    if path_raw is None or source_id is None or perturbation is None:
        return {"error": "path, source_entity_id, perturbation required",
                 "error_kind": "bad_input"}
    p, err = _validate_path(str(path_raw), must_exist=True)
    if err:
        return err
    try:
        from aurora_sdk import run as sdk_run
        r = sdk_run(p)
    except Exception as e:
        return _safe_error(e, "load_error")
    try:
        from fantasyai.aurora.system_model import simulate_intervention  # type: ignore
    except Exception as e:
        return _safe_error(e, "feature_unavailable")
    try:
        result = simulate_intervention(
            r.state, source_entity_id=str(source_id),
            perturbation=float(perturbation), max_depth=max_depth,
        )
    except Exception as e:
        return _safe_error(e, "intervene_error")
    # Normalise — simulate_intervention returns a dataclass or dict.
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    return _truncate_payload({"ok": True, "result": result})


# ---------------------------------------------------------------------------
# Tool: aurora_simulate
# ---------------------------------------------------------------------------

def aurora_simulate(args: Dict[str, Any]) -> Dict[str, Any]:
    """Forward-step the validated dynamics on the chosen target.

    Wraps ``simulate_forward`` from the system_model layer. Returns the
    trajectory in the same shape the frontend SIMULATE panel renders.
    """
    path_raw = args.get("path")
    if path_raw is None:
        return {"error": "path required", "error_kind": "bad_input"}
    p, err = _validate_path(str(path_raw), must_exist=True)
    if err:
        return err
    n_steps = int(args.get("n_steps", 50))
    n_steps = max(1, min(2000, n_steps))
    ci_threshold = float(args.get("ci_pause_threshold", 5.0))
    target_eid = args.get("target_entity_id")
    try:
        from aurora_sdk import run as sdk_run
        r = sdk_run(p)
    except Exception as e:
        return _safe_error(e, "load_error")
    try:
        from fantasyai.aurora.system_model import simulate_forward  # type: ignore
    except Exception as e:
        return _safe_error(e, "feature_unavailable")
    # Optional: override the simulator's target via state.forecast.target
    state = dict(r.state)
    if target_eid:
        ents = ((state.get("system_model") or {}).get("topology") or {}).get("entities") or []
        ent = next((e for e in ents if e.get("id") == target_eid), None)
        override = (ent or {}).get("data_column") or target_eid
        fc = dict(state.get("forecast") or {})
        fc["target"] = override
        state["forecast"] = fc
    try:
        result = simulate_forward(
            state, n_steps=n_steps, ci_pause_threshold=ci_threshold,
        )
    except Exception as e:
        return _safe_error(e, "simulate_error")
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    return _truncate_payload({"ok": True, "result": result})


# ---------------------------------------------------------------------------
# Tool registry & JSON schemas (for the MCP layer to advertise)
# ---------------------------------------------------------------------------

TOOLS = {
    "aurora_analyze": aurora_analyze,
    "aurora_load_bundle": aurora_load_bundle,
    "aurora_findings": aurora_findings,
    "aurora_forecast": aurora_forecast,
    "aurora_explain": aurora_explain,
    "aurora_intervene": aurora_intervene,
    "aurora_simulate": aurora_simulate,
}


# JSON-Schemas mirror the MCP tool spec format. These are advertised to
# the MCP client so it knows how to call each tool. We keep them simple
# (no $ref, no defs) so the schema fits comfortably in a tool-listing.
TOOL_SCHEMAS = {
    "aurora_analyze": {
        "name": "aurora_analyze",
        "description": (
            "Statistical analysis of a dataset (CSV, TSV, Parquet, XLSX): runs "
            "Aurora's battery of 19 research-grade methods on-device — anomaly "
            "detection (isolation forest + robust-z), change-point detection, "
            "trend and seasonality, correlation screening with FDR control, "
            "forecasting, causal system-model discovery, and more. Returns CITED "
            "findings (each carries its method, threshold, and claim_id), an "
            "overall confidence, and a fabricated_count that is contractually "
            "zero: every number is computed from the data, never generated. "
            "Changepoint findings carry a calibration block: the empirically "
            "measured false-fire rate for data shaped like this series, with a "
            "verdict downgrade to not_identifiable when the data cannot support "
            "the claim. Use "
            "this FIRST whenever a user asks to analyze data, find anomalies, "
            "check what changed, or wants real statistics instead of estimates. "
            "Read-only; local; compact summary unless full_bundle=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                          "description": "CSV path, existing run_dir, or .aurora.json"},
                "depth": {"type": "string",
                           "enum": ["auto", "quick", "standard", "full"]},
                "sections": {"type": "array", "items": {"type": "string"}},
                "full_bundle": {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
    },
    "aurora_load_bundle": {
        "name": "aurora_load_bundle",
        "description": (
            "Load a portable .aurora.json analysis bundle and verify its SHA-256 "
            "integrity hash (and Ed25519 signature when present) BEFORE trusting "
            "its findings. Use when someone shares an Aurora bundle and you need "
            "proof it is untampered. Returns run identity, confidence, "
            "fabricated_count, and findings-by-severity counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":   {"type": "string"},
                "verify": {"type": "boolean", "default": True},
            },
            "required": ["path"],
        },
    },
    "aurora_findings": {
        "name": "aurora_findings",
        "description": (
            "List the verified findings from an Aurora run or bundle: each one "
            "carries severity (crit/warn/info), the exact statistical method and "
            "threshold that produced it, a plain-language citation, and a "
            "claim_id for evidence drill-down via aurora_explain. Filter by "
            "severity or method. Use after aurora_analyze to enumerate what was "
            "actually found — quote findings from here instead of paraphrasing "
            "from memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":     {"type": "string"},
                "severity": {
                    "oneOf": [
                        {"type": "string", "enum": ["crit", "warn", "info"]},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "method":   {"type": "string"},
                "limit":    {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["path"],
        },
    },
    "aurora_forecast": {
        "name": "aurora_forecast",
        "description": (
            "Model-based forecast for the run's target column, fitted and "
            "validated on the actual data with the method disclosed. Returns "
            "point predictions with an honest horizon, or just the peak within "
            "horizon_hours (return_peak=true). Use for any 'what will X be / when "
            "does it peak' question instead of extrapolating by eye."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":           {"type": "string"},
                "horizon_hours":  {"type": "number"},
                "return_peak":    {"type": "boolean", "default": False},
            },
            "required": ["path"],
        },
    },
    "aurora_explain": {
        "name": "aurora_explain",
        "description": (
            "Full evidence for ONE finding by claim_id: the computed values "
            "behind the claim plus the method's registry spec — assumptions, "
            "parameters, and references. Use whenever you are about to cite, "
            "verify, or defend a specific statistical claim; this is the receipt, "
            "not a summary."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":     {"type": "string"},
                "claim_id": {"type": "string"},
            },
            "required": ["path", "claim_id"],
        },
    },
    "aurora_intervene": {
        "name": "aurora_intervene",
        "description": (
            "What-if intervention: perturb one variable in the data's discovered "
            "system model and propagate the shock through validated relationships "
            "(up to max_depth hops). Returns per-node deltas WITH confidence "
            "intervals. Use for 'what happens to Y if X changes by Δ' questions — "
            "answers come from the data's own causal graph, not from priors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":              {"type": "string"},
                "source_entity_id":  {"type": "string"},
                "perturbation":      {"type": "number"},
                "max_depth":         {"type": "integer", "default": 3},
            },
            "required": ["path", "source_entity_id", "perturbation"],
        },
    },
    "aurora_simulate": {
        "name": "aurora_simulate",
        "description": (
            "Simulate the system forward n_steps using dynamics fitted and "
            "validated on the data — and it PAUSES honestly when confidence "
            "intervals grow too wide to keep going, rather than extrapolating "
            "noise. Use for trajectory questions ('where is this heading') on a "
            "completed run; pass target_entity_id to simulate a specific node."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":                 {"type": "string"},
                "n_steps":              {"type": "integer", "minimum": 1, "maximum": 2000},
                "ci_pause_threshold":   {"type": "number"},
                "target_entity_id":     {"type": "string"},
            },
            "required": ["path"],
        },
    },
}

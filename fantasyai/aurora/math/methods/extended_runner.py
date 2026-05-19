"""
Run all v1.2-era extended methods on a DataFrame and aggregate the
findings.

This is the integration point that lets the 7 new methods (VAR, DTW,
BOCPD, Robust PCA, EMD, Kalman, Spectral Entropy) actually surface in
the Studio UI. Each method is independently safe — if it fails or
skips, the runner records the reason and continues.

The orchestrator:

  * Has a single per-method timeout budget (the methods themselves
    already use NumPy/SciPy stably, but as a defensive layer)
  * Collects ALL findings into one list with consistent claim_id
    numbering
  * Never raises out — partial failures are documented in the result
  * Returns both raw findings and a per-method status summary so the
    Studio's "advanced methods" tile can show "VAR skipped: needs ≥2
    numeric columns" honestly
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


# Per-method timeout in seconds. The methods are expected to finish
# faster than this; we cap as a defensive measure so a pathological
# input can't block the whole pipeline.
DEFAULT_METHOD_TIMEOUT_S = 60.0


def run_extended_methods(
    df: Any,
    *,
    target_col: Optional[str] = None,
    time_col: Optional[str] = None,
    timeout_s: float = DEFAULT_METHOD_TIMEOUT_S,
    enabled_methods: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run every v1.2 extended method on ``df`` and return a unified
    result document.

    Args:
        df:               pandas DataFrame.
        target_col:       Hint for univariate methods (Kalman, BOCPD,
                          Spectral Entropy, EMD). None → each method
                          picks its own default.
        time_col:         Name of the time column if present. Some
                          methods (none yet, but reserved) may use this.
        timeout_s:        Per-method wall-clock cap. A method exceeding
                          this is recorded as ``skipped:timeout``.
        enabled_methods:  Optional whitelist of method names. None =
                          run all 7.

    Returns:
        ``{
            "findings": [<Aurora finding dicts>],
            "per_method": {
                "var": {"status": "fit", "elapsed_s": 1.23},
                ...
            },
            "n_methods_run": int,
            "n_findings": int,
        }``
    """
    from .var import fit_var
    from .dtw import fit_dtw
    from .bocpd import fit_bocpd
    from .robust_pca import fit_robust_pca
    from .emd import fit_emd
    from .kalman import fit_kalman
    from .spectral_entropy import fit_spectral_entropy

    # Method registry. Each entry is (name, fit_function, kwargs builder).
    # The kwargs builder pulls per-method args from the caller's params.
    method_specs = [
        ("var",              fit_var,              lambda: {}),
        ("dtw",              fit_dtw,              lambda: {}),
        ("bocpd",            fit_bocpd,            lambda: {"target_col": target_col} if target_col else {}),
        ("robust_pca",       fit_robust_pca,       lambda: {}),
        ("emd",              fit_emd,              lambda: {"target_col": target_col} if target_col else {}),
        ("kalman",           fit_kalman,           lambda: {"target_col": target_col} if target_col else {}),
        ("spectral_entropy", fit_spectral_entropy, lambda: {"target_col": target_col} if target_col else {}),
    ]

    if enabled_methods is not None:
        method_specs = [m for m in method_specs if m[0] in enabled_methods]

    findings: List[Dict[str, Any]] = []
    per_method: Dict[str, Dict[str, Any]] = {}
    claim_seq = 0

    for name, fn, kwargs_builder in method_specs:
        t0 = time.perf_counter()
        try:
            kwargs = kwargs_builder()
            kwargs["claim_seq"] = claim_seq
            finding = fn(df, **kwargs)
            elapsed = time.perf_counter() - t0
            findings.append(finding)
            per_method[name] = {
                "status": str((finding.get("evidence") or {}).get("status", "fit")),
                "elapsed_s": round(elapsed, 3),
            }
            claim_seq += 1
        except Exception as e:
            elapsed = time.perf_counter() - t0
            LOG.exception(f"extended method {name} failed")
            per_method[name] = {
                "status": "failed",
                "elapsed_s": round(elapsed, 3),
                "error": f"{type(e).__name__}: {e}",
            }
            # Emit an honest "method failed" finding rather than dropping.
            findings.append({
                "severity": "info",
                "confidence": 0.0,
                "title": f"{name} crashed",
                "description": f"{type(e).__name__}: {e}",
                "method": name,
                "actions": [],
                "kind_hint": "method_failed",
                "claim_id": f"{name}-failed-{claim_seq:04d}",
                "fabricated": False,
                "evidence": {"status": "failed", "error": str(e)},
            })
            claim_seq += 1

    # Q3 Stream C: also run any installed Plugin SDK methods. Third-party
    # plugins register via the ``aurora_plugins`` entry-point group;
    # their findings flow alongside the 7 built-in v1.2 methods.
    plugin_block: Dict[str, Any] = {}
    try:
        from fantasyai.aurora.plugins import run_plugin_methods
        plugin_out = run_plugin_methods(
            df,
            target_col=target_col,
            time_col=time_col,
            timeout_s=timeout_s,
        )
        plugin_findings = plugin_out.get("findings") or []
        for f in plugin_findings:
            if isinstance(f, dict):
                # Renumber claim_ids to stay unique across built-ins + plugins.
                if not f.get("claim_id"):
                    f["claim_id"] = f"{f.get('method', 'plugin')}-{claim_seq:04d}"
                    claim_seq += 1
                findings.append(f)
        plugin_block = {
            "per_plugin":    plugin_out.get("per_plugin") or {},
            "plugins":       plugin_out.get("plugins") or [],
            "n_plugins_run": plugin_out.get("n_plugins_run") or 0,
        }
    except Exception:
        # Plugins are additive — never break the built-in run.
        plugin_block = {"per_plugin": {}, "plugins": [], "n_plugins_run": 0}

    return {
        "findings":      findings,
        "per_method":    per_method,
        "n_methods_run": len(method_specs),
        "n_findings":    len(findings),
        # Q3 Stream C: surface plugin telemetry alongside built-ins.
        "plugins":       plugin_block,
    }

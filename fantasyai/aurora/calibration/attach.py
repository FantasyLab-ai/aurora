"""
Attach calibration to findings — and let it change the verdict.

Contract (from the calibration design, enforced by tests):

  * Calibration NEVER blocks a finding. Any failure in fingerprinting,
    corpus loading, or lookup degrades to an explicit status on the
    finding; the finding itself always returns.
  * The raw statistic is never hidden. A downgraded finding keeps its
    full evidence block; calibration contextualises, it does not erase.
  * A verdict that depends on a threshold discloses the threshold: the
    configured FDR ceiling rides inside the calibration block.
  * Methods without a corpus get an explicit ``not_yet_calibrated``
    block — absence of calibration is a first-class, visible state.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .fingerprint import fingerprint_series
from .harness import CHANGEPOINT_FAMILY
from .lookup import CalibrationConfig, derive_remediation, lookup_calibration

LOG = logging.getLogger(__name__)

# The verdict string a downgraded finding carries. Kept lowercase-snake
# to match Aurora's other verdict vocabularies (granger, wavelet).
VERDICT_NOT_IDENTIFIABLE = "not_identifiable"


def apply_calibration_to_finding(
    finding: Dict[str, Any],
    values: Any,
    *,
    timestamps: Any = None,
    corpus: Optional[Dict[str, Any]] = None,
    config: Optional[CalibrationConfig] = None,
) -> Dict[str, Any]:
    """Fingerprint ``values``, look up the finding's method in the corpus,
    attach the calibration block, and downgrade the verdict when the
    measured false-fire rate exceeds the configured ceiling.

    Mutates and returns ``finding``. Never raises.
    """
    cfg = config or CalibrationConfig()
    method = str(finding.get("method") or "")
    try:
        if corpus is None:
            from .store import get_corpus_cached
            corpus = get_corpus_cached(cfg.corpus_version)

        fp = fingerprint_series(values, timestamps)
        block = lookup_calibration(fp, method, corpus, cfg)

        if block.get("status") in ("calibrated", "interpolated"):
            remediation = derive_remediation(block, fp, method, corpus, cfg)
            if remediation:
                block["remediation"] = remediation
            _maybe_downgrade(finding, block, cfg)

        finding["calibration"] = block
    except Exception as e:
        # Degrade, don't drop — and don't pretend either.
        LOG.warning("calibration attach failed for %s: %s", method, e)
        finding["calibration"] = {
            "status": "unavailable",
            "note": f"calibration lookup failed: {type(e).__name__}: {e}",
        }
    return finding


def _fired(finding: Dict[str, Any]) -> bool:
    """Did this changepoint finding actually claim a detection?"""
    ev = finding.get("evidence") or {}
    if ev.get("status") != "fit":
        return False
    cps = ev.get("change_points")
    if cps is None:
        cps = ev.get("changepoints")
    return bool(cps)


def _maybe_downgrade(finding: Dict[str, Any], block: Dict[str, Any],
                     cfg: CalibrationConfig) -> None:
    """Downgrade a positive finding whose measured false-fire rate
    exceeds the ceiling. The raw statistic stays; the verdict changes."""
    fdr = block.get("empirical_fdr")
    if not isinstance(fdr, (int, float)):
        return
    if not _fired(finding):
        return
    if fdr <= cfg.fdr_ceiling:
        return

    block["verdict_downgraded"] = True
    finding["verdict"] = VERDICT_NOT_IDENTIFIABLE
    finding["severity"] = "info"
    reason = (
        f" NOT IDENTIFIABLE at this calibration: the detector fires on "
        f"{fdr:.1%} of stationary null series shaped like this data "
        f"(ceiling {cfg.fdr_ceiling:.0%}). The detection above is reported, "
        f"not asserted."
    )
    finding["description"] = str(finding.get("description") or "") + reason


def annotate_findings(
    findings: List[Dict[str, Any]],
    df: Any,
    *,
    target_col: Optional[str] = None,
    corpus: Optional[Dict[str, Any]] = None,
    config: Optional[CalibrationConfig] = None,
) -> List[Dict[str, Any]]:
    """Annotate a batch of extended-method findings in place.

    Changepoint-family findings get the full lookup against the column
    they analysed; every other method gets an explicit
    ``not_yet_calibrated`` marker rather than silence.
    """
    cfg = config or CalibrationConfig()
    for f in findings:
        if not isinstance(f, dict):
            continue
        method = str(f.get("method") or "")
        try:
            if method in CHANGEPOINT_FAMILY:
                ev = f.get("evidence") or {}
                col = ev.get("target_col") or target_col
                if col is None or col not in getattr(df, "columns", []):
                    f["calibration"] = {
                        "status": "unavailable",
                        "note": (
                            "calibration could not identify the analysed "
                            "column to fingerprint"
                        ),
                    }
                    continue
                values = df[col].values
                apply_calibration_to_finding(
                    f, values, corpus=corpus, config=cfg
                )
            elif (f.get("evidence") or {}).get("status") == "fit":
                f["calibration"] = {
                    "status": "not_yet_calibrated",
                    "note": (
                        f"No calibration corpus covers {method!r} yet; "
                        "corpus v1 covers the changepoint family "
                        f"({', '.join(CHANGEPOINT_FAMILY)})."
                    ),
                }
        except Exception as e:  # belt over the belt — never break a run
            LOG.warning("annotate_findings failed for %s: %s", method, e)
            f.setdefault("calibration", {
                "status": "unavailable",
                "note": f"calibration annotation failed: {type(e).__name__}",
            })
    return findings

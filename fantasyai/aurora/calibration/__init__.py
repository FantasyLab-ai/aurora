"""
Aurora calibration engine.

For each method and each measurable data regime: how often does the
method produce a finding on data that contains nothing to find? The
answer is measured on seeded synthetic nulls, shipped as a versioned
corpus, and attached to findings at analysis time — including the power
to downgrade a verdict the data cannot support.

Public surface:

    fingerprint_series(values, timestamps)  -> RegimeFingerprint
    load_corpus(version)                    -> corpus dict (hash-verified)
    lookup_calibration(fp, method, corpus)  -> calibration block
    apply_calibration_to_finding(finding, values) -> finding (annotated)
    annotate_findings(findings, df)         -> findings (batch, in place)
    build_corpus(...)                       -> corpus dict (batch job)

Honest-by-construction rules live in lookup.py; production-fidelity
rules live in harness.py. Read those docstrings before changing either.
"""
from .fingerprint import RegimeFingerprint, fingerprint_series
from .generators import BASE_SEED, GENERATORS, generate_cell_trial, make_rng
from .harness import (
    CHANGEPOINT_FAMILY,
    DEFAULT_GRID,
    DEFAULT_TRIALS,
    METHOD_VERSIONS,
    PRODUCTION_ENTRYPOINTS,
    build_cell,
    build_corpus,
    run_changepoint_family,
    wilson_ci,
)
from .lookup import CalibrationConfig, derive_remediation, lookup_calibration
from .store import (
    CorpusIntegrityError,
    CorpusNotFoundError,
    get_corpus_cached,
    load_corpus,
    save_corpus,
)
from .attach import (
    VERDICT_NOT_IDENTIFIABLE,
    annotate_findings,
    apply_calibration_to_finding,
)

__all__ = [
    "RegimeFingerprint", "fingerprint_series",
    "BASE_SEED", "GENERATORS", "generate_cell_trial", "make_rng",
    "CHANGEPOINT_FAMILY", "DEFAULT_GRID", "DEFAULT_TRIALS",
    "METHOD_VERSIONS", "PRODUCTION_ENTRYPOINTS",
    "build_cell", "build_corpus", "run_changepoint_family", "wilson_ci",
    "CalibrationConfig", "derive_remediation", "lookup_calibration",
    "CorpusIntegrityError", "CorpusNotFoundError",
    "get_corpus_cached", "load_corpus", "save_corpus",
    "VERDICT_NOT_IDENTIFIABLE", "annotate_findings",
    "apply_calibration_to_finding",
]

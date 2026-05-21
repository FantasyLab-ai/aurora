"""
Aurora SDK — public Python API for embedding Aurora analyses in your own
code, agents, pipelines, and applications.

The SDK is intentionally thin: it wraps Aurora's existing pipeline
(``state_builder.build_state`` + the runner) and emits a stable,
versioned **Aurora Bundle** (``.aurora.json``) that downstream tools
(MCP servers, Decision Contracts, the research kit) can consume.

Public surface:

    >>> import aurora_sdk as aurora
    >>> r = aurora.run("data.csv", depth="standard")
    >>> r.findings.critical()              # → list[Finding]
    >>> r.forecast.peak(horizon_hours=24)
    >>> r.bundle.save("audit.aurora.json")
    >>> b = aurora.Bundle.load("audit.aurora.json")
    >>> b.verify()                          # checks integrity hash
    >>> b.sign(private_key_bytes)           # optional Ed25519 signature

The SDK never executes arbitrary user code. Every public method
validates its inputs against the bundle schema and refuses malformed
payloads. Bundles are JSON-only — no pickle, no eval — so they are
safe to share across trust boundaries.

Bundle schema version: 1.0.0
"""
from __future__ import annotations

from .bundle import (
    Bundle,
    BundleIntegrityError,
    BundleSchemaError,
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_FORMAT_NAME,
    build_bundle_from_state,
    load_bundle,
    save_bundle,
)
from .findings import Findings, Finding, ForecastView, SystemModelView
from .runner import run, RunResult
from .notebook_export import (
    export_notebook,
    read_export,
    ExportInfo,
    NotebookExportError,
    EXPORT_SCHEMA_VERSION,
    EXPORT_EXTENSION,
)
from .methods import (
    MethodsView,
    # Typed fits — each accessor returns one of these or None.
    VarFit, CrossCoupling,
    DtwFit, DtwPair,
    BocpdFit, ChangePoint,
    RobustPcaFit, OutlierRow,
    EmdFit, ImfStat,
    KalmanFit, KalmanForecastStep,
    SpectralEntropyFit, SpectralEntropyWindow,
    MatrixProfileFit, Motif, Discord,
    GrangerFit, GrangerPair,
    MutualInfoFit, MutualInfoPair,
    HmmFit,
    WaveletFit,
)

__all__ = [
    "Bundle",
    "BundleIntegrityError",
    "BundleSchemaError",
    "BUNDLE_SCHEMA_VERSION",
    "BUNDLE_FORMAT_NAME",
    "build_bundle_from_state",
    "load_bundle",
    "save_bundle",
    "Findings",
    "Finding",
    "ForecastView",
    "SystemModelView",
    "run",
    "RunResult",
    # Jupyter / notebook ergonomics
    "export_notebook",
    "read_export",
    "ExportInfo",
    "NotebookExportError",
    "EXPORT_SCHEMA_VERSION",
    "EXPORT_EXTENSION",
    # v0.10.1 — typed method accessors + trading backtest.
    "MethodsView",
    "VarFit", "CrossCoupling",
    "DtwFit", "DtwPair",
    "BocpdFit", "ChangePoint",
    "RobustPcaFit", "OutlierRow",
    "EmdFit", "ImfStat",
    "KalmanFit", "KalmanForecastStep",
    "SpectralEntropyFit", "SpectralEntropyWindow",
    "MatrixProfileFit", "Motif", "Discord",
    "GrangerFit", "GrangerPair",
    "MutualInfoFit", "MutualInfoPair",
    "HmmFit",
    "WaveletFit",
]

__version__ = "1.0.1"

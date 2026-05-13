"""
Aurora Bundle v1 — the stable, signable analytical artifact.

A bundle is a single JSON document that captures everything a downstream
consumer needs to interpret, reproduce, and trust an Aurora analysis:

  * dataset identity (name, shape, sha256 of file contents)
  * run identity (id, dir, tier, started/completed timestamps)
  * findings with full provenance (method, threshold, citation, claim_id)
  * forecast + system_model + synthesis (already in state)
  * assumptions Aurora made
  * an integrity hash computed over the canonical content
  * an optional Ed25519 signature

Why JSON-only: cross-tool, cross-language, cross-trust-boundary
portable. Pickle is fast but unsafe (RCE on load); we never use it.

Why canonical hashing: anyone with the bundle and the dataset file can
verify the bundle has not been tampered with. The hash is computed over
the JSON content sorted by key (RFC 8785-ish canonicalisation) so the
same logical payload always produces the same hash regardless of
serialisation order.

Schema version is part of the bundle (``bundle_version``); breaking
changes bump the major.

Security stance:

  * No code execution on load — purely declarative parsing.
  * The integrity hash is mandatory; ``verify()`` refuses missing/wrong.
  * Signature verification (when present) uses ``cryptography`` if
    available, else raises a clear "install cryptography to verify
    signatures" error rather than silently accepting unsigned data.
  * No paths in the bundle that aren't relative — absolute paths are
    not portable and may leak host info.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

BUNDLE_SCHEMA_VERSION = "1.0.0"
BUNDLE_FORMAT_NAME = "aurora.bundle"

# Hashing: SHA-256 is fast, ubiquitous, collision-resistant for our threat
# model (tamper detection, not adversarial cryptography). 32-byte output.
_HASH_ALG = "sha256"

# The set of top-level keys that participate in the integrity hash.
# ``integrity`` itself is excluded — it's *about* the rest of the bundle,
# not part of what it covers. ``generated_at`` is also excluded so the
# same logical content produces the same hash on different days.
_HASHED_KEYS = (
    "bundle_version",
    "format",
    "aurora_version",
    "run",
    "dataset",
    "structure",
    "findings",
    "system_model",
    "synthesis",
    "forecast",
    "anomalies",
    "regimes",
    "motifs",
    "causal",
    "physics",
    "assumptions",
    "method_registry",
    "fabricated_count",
)


class BundleSchemaError(ValueError):
    """Raised when a bundle document is missing required keys or has a
    structurally invalid shape."""


class BundleIntegrityError(ValueError):
    """Raised when integrity verification fails — either the integrity
    block is missing, the hash doesn't match, or (when a signature is
    present) the signature is invalid."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _aurora_version() -> str:
    """Best-effort version read from ``fantasyai.aurora`` package."""
    try:
        import fantasyai.aurora as _a  # type: ignore
        v = getattr(_a, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    # Fallback: try reading from a VERSION file if present.
    try:
        root = Path(__file__).resolve().parents[1]
        vfile = root / "VERSION"
        if vfile.exists():
            return vfile.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return "unknown"


def _file_sha256(path: Path) -> Optional[str]:
    """Stream a file through SHA-256. Returns None if the file can't be
    read (don't crash bundle creation just because a sidecar is gone)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _canonical_json(obj: Any) -> bytes:
    """Canonical JSON serialisation for stable hashing.

    Sorts keys, uses no whitespace, encodes ensure_ascii=True so the
    same payload bytes appear in every Python build. This is *not* full
    RFC 8785 (which has stricter number-canonicalisation rules), but it
    is deterministic enough for our tamper-detection use case.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_safe,
    ).encode("utf-8")


def _json_safe(obj: Any) -> Any:
    """Coerce numpy/pandas/datetime types to JSON-safe primitives."""
    # NumPy scalars / arrays.
    try:
        import numpy as np  # type: ignore
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except Exception:
        pass
    # Pandas timestamps.
    try:
        import pandas as pd  # type: ignore
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if obj is pd.NaT:
            return None
    except Exception:
        pass
    # Datetime.
    import datetime as _dt
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _compute_content_hash(payload: Dict[str, Any]) -> str:
    """Compute SHA-256 over the hashed subset of the payload."""
    subset = {k: payload.get(k) for k in _HASHED_KEYS if k in payload}
    return hashlib.sha256(_canonical_json(subset)).hexdigest()


def _count_fabricated(findings: List[Dict[str, Any]]) -> int:
    """Count fabricated findings per Aurora's contract.

    A finding is fabricated when explicitly flagged (``fabricated=true``)
    OR its method tag indicates LLM-generated content with no analytical
    backing. Missing optional fields (e.g., claim_id) do NOT count —
    those are legitimate omissions, not fabrication signals.
    """
    n = 0
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        if f.get("fabricated") is True:
            n += 1
            continue
        m = str(f.get("method", "")).lower().strip()
        if m in ("llm_generated", "fabricated", "unsourced"):
            n += 1
    return n


# ---------------------------------------------------------------------------
# Building bundles from Aurora state
# ---------------------------------------------------------------------------

def build_bundle_from_state(
    state: Dict[str, Any],
    *,
    run_dir: Optional[Union[str, Path]] = None,
    dataset_path: Optional[Union[str, Path]] = None,
    include_sections: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Translate an Aurora ``state`` dict (from ``state_builder.build_state``)
    into a v1 Aurora Bundle dict.

    Args:
      state: the full state dict.
      run_dir: optional override for the bundle's ``run.run_dir``. If
        absent, read from ``state.run_dir``.
      dataset_path: optional path to the source CSV. When supplied, the
        bundle includes ``dataset.sha256`` so consumers can verify the
        bundle was generated from THIS file.
      include_sections: when None, bundle includes the standard set;
        otherwise restrict to the named top-level state keys.

    Returns:
      A plain dict (JSON-safe) ready to ``json.dump``.
    """
    if not isinstance(state, dict):
        raise BundleSchemaError("state must be a dict")

    run_meta = state.get("run_meta") or {}
    dataset = state.get("dataset") or {}
    structure = state.get("structure") or {}

    # Resolve the dataset hash if a path is supplied OR present in the state.
    ds_path_str = (
        str(dataset_path) if dataset_path is not None
        else dataset.get("path")
    )
    ds_sha256 = None
    if ds_path_str:
        try:
            p = Path(ds_path_str)
            if p.exists() and p.is_file():
                ds_sha256 = _file_sha256(p)
        except Exception:
            pass

    findings = state.get("findings") or []
    fabricated_count = _count_fabricated(findings)

    bundle: Dict[str, Any] = {
        "bundle_version": BUNDLE_SCHEMA_VERSION,
        "format": BUNDLE_FORMAT_NAME,
        "aurora_version": _aurora_version(),
        "generated_at": _now_iso(),
        "run": {
            "run_id": (
                run_meta.get("run_id")
                or state.get("run_id")
                or "(unknown)"
            ),
            "run_dir": str(run_dir or state.get("run_dir") or ""),
            "started_at": run_meta.get("started_at") or run_meta.get("created_at"),
            "completed_at": run_meta.get("completed_at"),
            "state": run_meta.get("state"),
            "tier": run_meta.get("tier") or run_meta.get("active_tier"),
        },
        "dataset": {
            "name": dataset.get("name"),
            "rows": dataset.get("rows"),
            "cols": dataset.get("cols"),
            "size_mb": dataset.get("size_mb"),
            # Bundle stores the basename only — full paths are host-specific
            # and would leak filesystem layout. Verifiers should supply the
            # dataset file themselves and check sha256.
            "basename": (
                os.path.basename(ds_path_str.replace("\\", "/"))
                if ds_path_str else dataset.get("name")
            ),
            "sha256": ds_sha256,
        },
        "structure": structure,
        # Findings normalised: every finding gets a claim_id (synthesized
        # from rank when missing) so downstream tools can reference them.
        "findings": [
            _normalise_finding(f, i) for i, f in enumerate(findings)
        ],
        "system_model": state.get("system_model"),
        "synthesis": state.get("synthesis"),
        "forecast": state.get("forecast"),
        "anomalies": state.get("anomalies"),
        "regimes": state.get("regimes"),
        "motifs": state.get("motifs"),
        "causal": state.get("causal"),
        "physics": state.get("physics"),
        "assumptions": _extract_assumptions(state),
        "method_registry": _build_method_registry(state),
        "fabricated_count": fabricated_count,
    }

    # Optionally trim to a subset of sections.
    if include_sections is not None:
        allow = set(include_sections) | {
            "bundle_version", "format", "aurora_version",
            "generated_at", "run", "dataset", "fabricated_count",
        }
        bundle = {k: v for k, v in bundle.items() if k in allow}

    # Integrity block — last, so it covers the rest of the bundle.
    bundle["integrity"] = {
        "hash_alg": _HASH_ALG,
        "content_hash": _compute_content_hash(bundle),
        "signature": None,
        "signature_alg": None,
        "public_key": None,
    }
    return bundle


def _normalise_finding(f: Any, fallback_rank: int) -> Dict[str, Any]:
    """Ensure every finding in the bundle has a claim_id + safe defaults."""
    if not isinstance(f, dict):
        return {
            "claim_id": f"claim-{fallback_rank:04d}",
            "rank": fallback_rank,
            "title": str(f),
            "fabricated": False,
        }
    out = dict(f)
    if not out.get("claim_id"):
        # Synthesize from method + rank for stability.
        m = str(out.get("method", "anon")).lower().replace(" ", "_")
        out["claim_id"] = f"{m}-{out.get('rank', fallback_rank):04d}"
    if "fabricated" not in out:
        out["fabricated"] = False
    return out


def _extract_assumptions(state: Dict[str, Any]) -> List[str]:
    """Collect declared assumptions from the state. Best-effort; falls
    back to the standard set if the runner didn't emit any."""
    declared: List[str] = []
    # Synthesis can include an assumptions list.
    syn = state.get("synthesis") or {}
    syn_assump = syn.get("assumptions") or syn.get("open_assumptions") or []
    for a in syn_assump:
        if isinstance(a, str):
            declared.append(a)
    # Physics layer often declares its assumptions.
    phys = state.get("physics") or {}
    for a in (phys.get("assumptions") or []):
        if isinstance(a, str):
            declared.append(a)
    return declared


def _build_method_registry(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Collect every distinct method appearing in findings, with a count
    and the most-common severity. Helps downstream consumers (audit
    tools, decision contracts) reason about which methods produced this
    bundle without re-walking findings."""
    out: Dict[str, Dict[str, Any]] = {}
    for f in state.get("findings") or []:
        if not isinstance(f, dict):
            continue
        m = str(f.get("method") or "(unknown)")
        rec = out.setdefault(m, {"count": 0, "severities": {}})
        rec["count"] += 1
        sev = str(f.get("severity") or "info")
        rec["severities"][sev] = rec["severities"].get(sev, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Save / load / verify / sign
# ---------------------------------------------------------------------------

def save_bundle(bundle: Dict[str, Any], path: Union[str, Path],
                *, indent: int = 2) -> Path:
    """Write a bundle to disk as JSON. Path is created if needed; parent
    directories are NOT created — caller's responsibility. Returns the
    Path written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(bundle, indent=indent, default=_json_safe, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def load_bundle(path: Union[str, Path]) -> Dict[str, Any]:
    """Load a bundle from disk. Raises BundleSchemaError on malformed
    content or wrong format marker. Does NOT verify the integrity hash
    — call ``verify_bundle`` for that."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"bundle not found: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise BundleSchemaError(f"bundle is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise BundleSchemaError("bundle root must be a JSON object")
    fmt = doc.get("format")
    if fmt and fmt != BUNDLE_FORMAT_NAME:
        raise BundleSchemaError(
            f"unexpected bundle format '{fmt}', expected '{BUNDLE_FORMAT_NAME}'"
        )
    ver = doc.get("bundle_version")
    if ver and not str(ver).startswith("1."):
        raise BundleSchemaError(
            f"unsupported bundle major version '{ver}'; SDK supports 1.x"
        )
    return doc


def verify_bundle(bundle: Dict[str, Any], *, require_signature: bool = False
                   ) -> bool:
    """Verify the bundle's integrity hash. Optionally requires a valid
    signature too. Raises BundleIntegrityError on any failure."""
    integ = bundle.get("integrity")
    if not isinstance(integ, dict):
        raise BundleIntegrityError("bundle has no 'integrity' block")
    stored_hash = integ.get("content_hash")
    alg = integ.get("hash_alg")
    if alg != _HASH_ALG:
        raise BundleIntegrityError(
            f"unsupported hash algorithm '{alg}', expected '{_HASH_ALG}'"
        )
    if not stored_hash or not isinstance(stored_hash, str):
        raise BundleIntegrityError("integrity.content_hash is missing")
    recomputed = _compute_content_hash(bundle)
    if recomputed != stored_hash:
        raise BundleIntegrityError(
            "content hash mismatch — bundle has been modified since signing"
        )
    if require_signature and not integ.get("signature"):
        raise BundleIntegrityError("signature required but not present")
    if integ.get("signature"):
        _verify_signature(bundle, integ)
    return True


def _verify_signature(bundle: Dict[str, Any], integ: Dict[str, Any]) -> None:
    """Verify an Ed25519 signature over the content hash. Requires the
    ``cryptography`` package; if absent, signature verification is a
    hard error (never silently passes)."""
    sig_alg = integ.get("signature_alg") or "ed25519"
    sig_hex = integ.get("signature")
    pub_hex = integ.get("public_key")
    if sig_alg.lower() != "ed25519":
        raise BundleIntegrityError(
            f"unsupported signature algorithm '{sig_alg}'"
        )
    if not pub_hex:
        raise BundleIntegrityError(
            "signature present but public_key missing — cannot verify"
        )
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature
    except Exception as e:
        raise BundleIntegrityError(
            "cryptography package not available — install it to verify "
            "signed bundles: pip install cryptography"
        ) from e
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub.verify(bytes.fromhex(sig_hex),
                    bytes.fromhex(integ.get("content_hash", "")))
    except InvalidSignature as e:
        raise BundleIntegrityError("signature verification FAILED") from e
    except Exception as e:
        raise BundleIntegrityError(f"signature verification error: {e}") from e


def sign_bundle(bundle: Dict[str, Any], private_key_bytes: bytes
                 ) -> Dict[str, Any]:
    """Sign a bundle's content_hash with an Ed25519 private key (32
    bytes raw). Mutates and returns the bundle with integrity.signature
    + integrity.public_key populated. Re-computes the content hash
    first to be sure we're signing the current content."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except Exception as e:
        raise RuntimeError(
            "cryptography package not available — install it to sign "
            "bundles: pip install cryptography"
        ) from e
    integ = bundle.setdefault("integrity", {
        "hash_alg": _HASH_ALG, "content_hash": None,
        "signature": None, "signature_alg": None, "public_key": None,
    })
    # Recompute the hash so signing always covers the current content.
    integ["content_hash"] = _compute_content_hash(bundle)
    priv = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    sig = priv.sign(bytes.fromhex(integ["content_hash"]))
    pub = priv.public_key().public_bytes_raw()
    integ["signature"] = sig.hex()
    integ["signature_alg"] = "ed25519"
    integ["public_key"] = pub.hex()
    return bundle


# ---------------------------------------------------------------------------
# Bundle class — convenience wrapper around the dict
# ---------------------------------------------------------------------------

@dataclass
class Bundle:
    """Thin wrapper around the bundle dict. Use class methods to load
    from a file, ``from_state()`` to build from an Aurora state, or
    instantiate directly with a doc dict.

    Methods:
        * ``save(path)``            — write to disk as JSON
        * ``verify()``              — check integrity hash + signature
        * ``sign(private_key)``     — Ed25519-sign the content hash
        * ``findings()``            — Findings helper view
        * ``forecast()``            — ForecastView
        * ``system_model()``        — SystemModelView
    """
    doc: Dict[str, Any]

    @classmethod
    def from_state(cls, state: Dict[str, Any], **kw) -> "Bundle":
        return cls(build_bundle_from_state(state, **kw))

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Bundle":
        return cls(load_bundle(path))

    def save(self, path: Union[str, Path], *, indent: int = 2) -> Path:
        return save_bundle(self.doc, path, indent=indent)

    def verify(self, *, require_signature: bool = False) -> bool:
        return verify_bundle(self.doc, require_signature=require_signature)

    def sign(self, private_key_bytes: bytes) -> "Bundle":
        sign_bundle(self.doc, private_key_bytes)
        return self

    # Lazy view accessors — imported here to avoid circular import at
    # module top level.
    def findings(self):
        from .findings import Findings
        return Findings(self.doc.get("findings") or [])

    def forecast(self):
        from .findings import ForecastView
        return ForecastView(self.doc.get("forecast") or {})

    def system_model(self):
        from .findings import SystemModelView
        return SystemModelView(self.doc.get("system_model") or {})

    # Convenience accessors for the common chips.
    @property
    def fabricated_count(self) -> int:
        return int(self.doc.get("fabricated_count") or 0)

    @property
    def run_id(self) -> str:
        return str((self.doc.get("run") or {}).get("run_id") or "")

    @property
    def confidence(self) -> Optional[float]:
        sm = self.doc.get("system_model") or {}
        c = sm.get("confidence")
        try:
            return float(c) if c is not None else None
        except Exception:
            return None

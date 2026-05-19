"""
Bundle attestation: integrity + signature + signer-trust check.

Three independent checks roll up into one ``AttestationReport``:

  1. **Integrity** — recompute the bundle's content hash and compare
     to the value stored in `bundle.integrity.content_hash`. This is
     the same check `aurora_sdk.Bundle.verify()` does; we re-run it
     here so the attestation reporting is a one-stop call.

  2. **Signature** — when `bundle.integrity.signature` + `.public_key`
     are present, verify the Ed25519 signature against the bundle
     content. Skipped (with `signature_present=False`) when no
     signature is attached.

  3. **Signer trust** — when the signature verifies, look up the
     public key in the workspace's trusted-signer registry. Returns
     the matched signer's label + domains, or `untrusted_signer=True`
     when the key isn't registered.

Glass-box contract: `AttestationReport.summary` is True ONLY when ALL
three checks pass. A bundle that verifies but whose signer isn't in
the registry gets `summary=False, untrusted_signer=True` so the
caller can distinguish "tampered" from "signed by an unknown party."
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .registry import is_trusted, list_trusted_signers


@dataclass
class AttestationReport:
    """The result of attesting a bundle."""
    summary:              bool      # True iff integrity + sig + trust all pass
    integrity_ok:         bool = False
    integrity_error:      Optional[str] = None
    signature_present:    bool = False
    signature_ok:         bool = False
    signature_error:      Optional[str] = None
    public_key_hex:       Optional[str] = None
    trusted_signer:       bool = False
    untrusted_signer:     bool = False
    signer_label:         Optional[str] = None
    signer_domains:       List[str] = field(default_factory=list)
    bundle_run_id:        Optional[str] = None
    bundle_content_hash:  Optional[str] = None
    bundle_dataset_sha256: Optional[str] = None
    warnings:             List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def attest_bundle(
    bundle: Dict[str, Any],
    *,
    workspace_id: Optional[str] = None,
    require_signature: bool = False,
) -> AttestationReport:
    """Attest a bundle.

    Args:
        bundle: the bundle document (as dict) — typically the result of
            `aurora_sdk.Bundle.load(path).doc` or a freshly-built bundle.
        workspace_id: which workspace's trusted-signer registry to use.
        require_signature: when True, an unsigned bundle fails attestation.
            When False (default), an unsigned bundle that integrity-checks
            yields summary=True with `signature_present=False` left in
            the report so the caller can decide.

    Returns AttestationReport.
    """
    if not isinstance(bundle, dict):
        return AttestationReport(
            summary=False,
            integrity_error="bundle must be a dict",
        )
    report = AttestationReport(summary=False)
    integrity = bundle.get("integrity") or {}
    report.bundle_run_id = (bundle.get("run") or {}).get("run_id")
    report.bundle_content_hash = integrity.get("content_hash")
    report.bundle_dataset_sha256 = (bundle.get("dataset") or {}).get("sha256")

    # ---- Integrity ---------------------------------------------------
    try:
        from aurora_sdk.bundle import verify_bundle
        ok = verify_bundle(bundle, require_signature=False)
        report.integrity_ok = bool(ok)
        if not ok:
            report.integrity_error = "content hash mismatch (bundle tampered or stale)"
    except Exception as e:
        report.integrity_ok = False
        report.integrity_error = f"{type(e).__name__}: {e}"

    # ---- Signature ---------------------------------------------------
    sig_hex = integrity.get("signature")
    pk_hex = integrity.get("public_key")
    if sig_hex and pk_hex:
        report.signature_present = True
        report.public_key_hex = str(pk_hex).lower()
        try:
            from aurora_sdk.bundle import verify_bundle
            ok = verify_bundle(bundle, require_signature=True)
            report.signature_ok = bool(ok)
            if not ok:
                report.signature_error = "signature verify failed"
        except Exception as e:
            report.signature_ok = False
            report.signature_error = f"{type(e).__name__}: {e}"
        # Trust check.
        if report.signature_ok and pk_hex:
            if is_trusted(str(pk_hex), workspace_id=workspace_id):
                report.trusted_signer = True
                for s in list_trusted_signers(workspace_id):
                    if str(s.get("public_key_hex") or "").lower() == str(pk_hex).lower():
                        report.signer_label = s.get("label")
                        report.signer_domains = list(s.get("domains") or [])
                        break
            else:
                report.untrusted_signer = True
    else:
        report.signature_present = False

    # ---- Summary -----------------------------------------------------
    if require_signature and not report.signature_present:
        report.summary = False
        report.warnings.append("signature required but not present")
    elif not report.integrity_ok:
        report.summary = False
    elif report.signature_present and not report.signature_ok:
        report.summary = False
    elif report.signature_present and report.untrusted_signer:
        # Signed but the key isn't in our registry — refuse summary=True
        # so a caller doing a one-shot trust check doesn't silently
        # accept a stranger's bundle.
        report.summary = False
        report.warnings.append(
            f"signature verified but signer {pk_hex[:16]}… is not in "
            f"the trusted-signer registry"
        )
    else:
        # Integrity OK + (unsigned OR signed-by-trusted-signer).
        report.summary = True
    return report

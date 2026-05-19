"""
Aurora attestation service (v2.0 Stream D).

Aurora Bundles already carry SHA-256 content hashes + optional
Ed25519 signatures (see `aurora_sdk.bundle.sign_bundle` /
`verify_bundle`). What v1.1 didn't ship is a clean way to ask
"is this bundle signed by a TRUSTED party?" — the signature
verifies, but verification alone doesn't tell you whose key signed it.

This module ships:

  * **Trusted-signer registry** — a per-workspace JSON file of public
    keys + labels. Adding a signer is an explicit, audited action.

  * **`attest_bundle(bundle)`** — verifies the bundle's integrity +
    cross-checks the signing public key against the registry.
    Returns a structured ``AttestationReport`` covering integrity,
    signature, signer identity, and the full set of trust signals.

  * **Public attestation endpoints** — `/api/attest/verify` lets any
    Aurora installation verify a bundle against its trusted-signer
    set. Useful when a teammate sends you an `.aurora.json` and you
    want a one-shot trust check before consuming it.

This is the foundation Aurora Cloud Phase 3 will lean on to publish
attestation receipts (signed verification records) to a public
ledger. The local-first verify path stays available either way.
"""
from __future__ import annotations

from .verify import (
    attest_bundle,
    AttestationReport,
)
from .registry import (
    list_trusted_signers,
    add_trusted_signer,
    remove_trusted_signer,
    trusted_signers_file,
    TrustedSigner,
)

__all__ = [
    "attest_bundle",
    "AttestationReport",
    "list_trusted_signers",
    "add_trusted_signer",
    "remove_trusted_signer",
    "trusted_signers_file",
    "TrustedSigner",
]

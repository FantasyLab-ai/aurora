"""
Per-workspace trusted-signer registry.

The registry lives at:

    <workspace_dir>/attestation/trusted_signers.json

Shape::

    {
      "signers": [
        {"public_key_hex":  "abcd1234…",
          "label":           "FantasyLab signing key (canonical)",
          "added_at":        1716130800.0,
          "added_by":        "alice@example.com",
          "domains":         ["climate", "biomed"],
          "notes":           "rotated 2026-04-01"},
        ...
      ],
      "schema_version": "1"
    }

Adding a signer is an explicit, audited action — there are no
"default trusted signers" baked into Aurora. Public keys are stored
as hex (`bytes.hex()` of the raw 32-byte Ed25519 verifying key),
which is what `aurora_sdk.bundle.sign_bundle` already produces.

We refuse to add malformed keys (wrong length, non-hex) at write time
so the registry can't accumulate junk that breaks attest_bundle.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


_WRITE_LOCK = threading.Lock()

# Ed25519 public keys are 32 bytes → 64 hex chars.
_PUBKEY_LEN_BYTES = 32
_PUBKEY_LEN_HEX = _PUBKEY_LEN_BYTES * 2


@dataclass
class TrustedSigner:
    public_key_hex: str
    label:          Optional[str] = None
    added_at:       Optional[float] = None
    added_by:       Optional[str] = None
    domains:        List[str] = field(default_factory=list)
    notes:          Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _workspace_dir(workspace_id: Optional[str]) -> Path:
    try:
        from fantasyai.aurora.auth import workspace_dir
        return workspace_dir(workspace_id)
    except Exception:
        env = os.environ.get("AURORA_DATA_ROOT", "").strip()
        if env:
            return Path(env).expanduser() / "workspaces" / (workspace_id or "default")
        return Path.home() / ".aurora"


def trusted_signers_file(workspace_id: Optional[str] = None) -> Path:
    p = _workspace_dir(workspace_id) / "attestation" / "trusted_signers.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(workspace_id: Optional[str]) -> Dict[str, Any]:
    path = trusted_signers_file(workspace_id)
    if not path.exists():
        return {"signers": [], "schema_version": "1"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"signers": [], "schema_version": "1"}
        if not isinstance(data.get("signers"), list):
            data["signers"] = []
        return data
    except Exception:
        return {"signers": [], "schema_version": "1"}


def _save(workspace_id: Optional[str], data: Dict[str, Any]) -> None:
    path = trusted_signers_file(workspace_id)
    with _WRITE_LOCK:
        try:
            path.write_text(
                json.dumps(data, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass


def _validate_pubkey(hex_str: str) -> str:
    """Return the canonical-lowercase hex form. Raise if invalid."""
    if not isinstance(hex_str, str):
        raise ValueError("public_key_hex must be a string")
    s = hex_str.strip().lower()
    if len(s) != _PUBKEY_LEN_HEX:
        raise ValueError(
            f"Ed25519 public key must be {_PUBKEY_LEN_HEX} hex chars "
            f"({_PUBKEY_LEN_BYTES} bytes); got {len(s)}"
        )
    try:
        bytes.fromhex(s)
    except ValueError:
        raise ValueError("public_key_hex contains non-hex characters")
    return s


def list_trusted_signers(workspace_id: Optional[str] = None
                          ) -> List[Dict[str, Any]]:
    return list(_load(workspace_id).get("signers") or [])


def add_trusted_signer(
    public_key_hex: str,
    *,
    workspace_id: Optional[str] = None,
    label: Optional[str] = None,
    added_by: Optional[str] = None,
    domains: Optional[List[str]] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Add (or update) a trusted signer. Idempotent on public_key_hex."""
    pk = _validate_pubkey(public_key_hex)
    data = _load(workspace_id)
    signers = list(data.get("signers") or [])
    now = time.time()
    found = False
    for e in signers:
        if str(e.get("public_key_hex") or "").lower() == pk:
            # Update existing entry rather than duplicating.
            e["public_key_hex"] = pk
            if label is not None:
                e["label"] = label
            if added_by is not None:
                e["added_by"] = added_by
            if domains is not None:
                e["domains"] = list(domains)
            if notes is not None:
                e["notes"] = notes
            e["updated_at"] = now
            found = True
            break
    if not found:
        signers.append({
            "public_key_hex": pk,
            "label":          label,
            "added_at":       now,
            "added_by":       added_by,
            "domains":        list(domains or []),
            "notes":          notes,
        })
    data["signers"] = signers
    _save(workspace_id, data)
    return next(e for e in signers
                if str(e.get("public_key_hex") or "").lower() == pk)


def remove_trusted_signer(
    public_key_hex: str,
    *,
    workspace_id: Optional[str] = None,
) -> bool:
    """Remove a signer. Returns True if something actually changed."""
    try:
        pk = _validate_pubkey(public_key_hex)
    except Exception:
        return False
    data = _load(workspace_id)
    signers = list(data.get("signers") or [])
    new_signers = [e for e in signers
                    if str(e.get("public_key_hex") or "").lower() != pk]
    if len(new_signers) == len(signers):
        return False
    data["signers"] = new_signers
    _save(workspace_id, data)
    return True


def is_trusted(
    public_key_hex: str,
    *,
    workspace_id: Optional[str] = None,
) -> bool:
    try:
        pk = _validate_pubkey(public_key_hex)
    except Exception:
        return False
    for e in list_trusted_signers(workspace_id):
        if str(e.get("public_key_hex") or "").lower() == pk:
            return True
    return False

"""
Token verification + workspace resolution.

Tokens are looked up from one of two sources (env > file):

  1. ``AURORA_TOKEN_<WORKSPACE>=<token>`` env vars. The workspace id
     is the lowercased suffix. Example::

         AURORA_TOKEN_ALICE=abc...   →  token "abc..." maps to workspace "alice"

  2. A JSON file at ``AURORA_TOKEN_FILE``. Shape::

         {
           "abc...": {"workspace_id": "alice", "label": "Alice's research"},
           "xyz...": {"workspace_id": "bob",   "label": "Bob's prod"}
         }

Env vars override the file. The file is convenient for fleets; env is
convenient for ad-hoc / small deployments. Both can coexist.

When ``AURORA_AUTH_REQUIRED`` is unset / false, ``auth_required`` is a
no-op that returns the ``DEFAULT_WORKSPACE`` context.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .workspaces import DEFAULT_WORKSPACE


@dataclass(frozen=True)
class WorkspaceContext:
    """The identity attached to an authenticated request."""
    workspace_id: str
    label: Optional[str] = None
    # The token's identity prefix, NEVER the full secret. Safe to log.
    token_id: Optional[str] = None
    # True if the request flowed through auth (False = anonymous /
    # auth-not-required mode). Used by callers that want to grant
    # extra permissions to authenticated callers even when auth is
    # optional globally.
    authenticated: bool = False


class AuthError(Exception):
    """Raised when a request can't be authenticated."""

    def __init__(self, message: str, *, http_status: int = 401):
        super().__init__(message)
        self.http_status = http_status


# ----------------------------------------------------------------------
# Token resolution
# ----------------------------------------------------------------------

def _load_token_file(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    if path is None:
        env_path = os.environ.get("AURORA_TOKEN_FILE", "").strip()
        if not env_path:
            return {}
        path = Path(env_path).expanduser()
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # Coerce values that are bare strings into a workspace_id-only dict.
    out: Dict[str, Dict[str, Any]] = {}
    for tok, meta in data.items():
        if isinstance(meta, str):
            out[tok] = {"workspace_id": meta, "label": None}
        elif isinstance(meta, dict):
            out[tok] = {
                "workspace_id": str(meta.get("workspace_id") or "anonymous"),
                "label": meta.get("label"),
            }
    return out


def _load_env_tokens() -> Dict[str, Dict[str, Any]]:
    """Walk ``AURORA_TOKEN_<workspace>`` env vars and assemble a lookup."""
    out: Dict[str, Dict[str, Any]] = {}
    prefix = "AURORA_TOKEN_"
    for name, value in os.environ.items():
        if not name.startswith(prefix):
            continue
        # AURORA_TOKEN_FILE is a special env var, not a token.
        if name == "AURORA_TOKEN_FILE":
            continue
        workspace = name[len(prefix):].lower().strip()
        if not workspace or not value.strip():
            continue
        out[value.strip()] = {
            "workspace_id": workspace,
            "label": None,
        }
    return out


def list_tokens() -> Dict[str, Dict[str, Any]]:
    """Combined token table from env (priority) + file. Tokens are
    NEVER logged; this returns them so callers can implement their
    own admin endpoints. Be careful what you do with the return value.
    """
    table = dict(_load_token_file())
    table.update(_load_env_tokens())
    return table


def resolve_token(token: str) -> Optional[WorkspaceContext]:
    """Return the WorkspaceContext for a token, or None if unknown."""
    if not token:
        return None
    table = list_tokens()
    meta = table.get(token.strip())
    if not meta:
        return None
    return WorkspaceContext(
        workspace_id=str(meta.get("workspace_id") or "anonymous"),
        label=meta.get("label"),
        token_id=token[:6] + "…",
        authenticated=True,
    )


# ----------------------------------------------------------------------
# Request integration
# ----------------------------------------------------------------------

def _extract_token(request_like: Any) -> Optional[str]:
    """Pull a bearer token from a Flask-style request object.

    Order: ``Authorization: Bearer ...`` → ``X-Aurora-Token`` →
    ``?token=...``. None of these is canonical; the lookup is
    deliberately tolerant so users of curl, JS fetch, and old shell
    aliases all work.
    """
    if request_like is None:
        return None
    # Authorization: Bearer <token>
    auth_header = (getattr(request_like, "headers", {}) or {}).get("Authorization") \
                if hasattr(request_like, "headers") else None
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    # X-Aurora-Token: <token>
    if hasattr(request_like, "headers"):
        x_tok = request_like.headers.get("X-Aurora-Token")
        if x_tok:
            return x_tok.strip()
    # ?token=<token>
    if hasattr(request_like, "args"):
        q_tok = request_like.args.get("token")
        if q_tok:
            return q_tok.strip()
    return None


def auth_required(request_like: Any) -> WorkspaceContext:
    """Resolve the workspace context for a request.

    Behaviour matrix:

        AURORA_AUTH_REQUIRED unset / "0":
            * Token present + valid → return its WorkspaceContext.
            * Otherwise return anonymous DEFAULT_WORKSPACE context.

        AURORA_AUTH_REQUIRED="1":
            * Token present + valid → return its WorkspaceContext.
            * Missing or invalid token → raise AuthError(401).
    """
    required = os.environ.get("AURORA_AUTH_REQUIRED", "").strip().lower() in (
        "1", "true", "yes",
    )
    token = _extract_token(request_like)
    if token:
        ctx = resolve_token(token)
        if ctx:
            return ctx
        if required:
            raise AuthError("invalid or unknown token", http_status=401)
    if required:
        raise AuthError("authentication required", http_status=401)
    return WorkspaceContext(
        workspace_id=DEFAULT_WORKSPACE,
        label="anonymous / single-tenant",
        token_id=None,
        authenticated=False,
    )

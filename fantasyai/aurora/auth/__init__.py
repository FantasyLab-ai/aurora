"""
Aurora Cloud Phase 2: multi-tenant auth + per-user workspaces.

The Phase 1 cloud deploy is single-tenant — anyone who can reach the
URL has full control of the server. Phase 2 adds:

  * **Token-based auth** — Bearer tokens map to a workspace identity.
    Tokens are looked up from ``AURORA_TOKEN_FILE`` (a JSON dict of
    ``{token: {workspace_id, label}}``) or from individual env vars
    matching ``AURORA_TOKEN_<workspace>``.

  * **Workspace scoping** — every authenticated request resolves to a
    workspace whose data directory is ``${AURORA_DATA_ROOT}/<workspace_id>``.
    Runs, knowledge-bank state, contracts, and uploads all live there.
    Two workspaces can never see each other's data.

  * **Usage logging** — every authenticated request appends a usage
    event to ``${AURORA_USAGE_LOG}`` (default
    ``${AURORA_DATA_ROOT}/usage.jsonl``). The aggregator that turns
    those events into billing rollups lives in Aurora Cloud Phase 3;
    this module just ships the recorder.

Auth is **opt-in**. Set ``AURORA_AUTH_REQUIRED=1`` to enforce it; the
default is unchanged (no auth) so single-user local installs still
work without changes.

The public surface is:

    aurora.auth.require_auth(request) -> WorkspaceContext
    aurora.auth.workspace_dir(workspace_id) -> Path
    aurora.auth.record_usage(workspace_id, endpoint, ...)

Plus the Flask integration helpers in ``auth.middleware``.
"""
from __future__ import annotations

from .tokens import (
    WorkspaceContext,
    AuthError,
    auth_required,
    resolve_token,
    list_tokens,
)
from .workspaces import (
    workspace_dir,
    data_root,
    DEFAULT_WORKSPACE,
)
from .usage import (
    record_usage,
    read_usage_events,
    usage_log_path,
)

__all__ = [
    "WorkspaceContext",
    "AuthError",
    "auth_required",
    "resolve_token",
    "list_tokens",
    "workspace_dir",
    "data_root",
    "DEFAULT_WORKSPACE",
    "record_usage",
    "read_usage_events",
    "usage_log_path",
]

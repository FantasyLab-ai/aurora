"""
Action runners — webhook, log, file. Every action is JSON-declarative
and has audited security guards.

Security model:

  * **Webhook**: only http/https scheme; hostname must resolve to a
    *public* (non-private, non-loopback, non-link-local, non-multicast)
    address by default. Override via ``ALLOW_LOCAL_WEBHOOKS=True`` for
    testing (the engine module-level flag; not per-contract). Requests
    use a strict timeout (default 10s, never more than 30s) and a 1 MB
    request-body cap. Authorization headers are accepted but never
    logged in plain — the audit record only stores a redacted form.
  * **Log**: writes to Python ``logging`` at the requested level. Never
    raises (a failing logger is not a contract failure).
  * **File**: append-only to a path *under* ``AURORA_CONTRACTS_OUTPUT``
    (defaults to ``~/.aurora/contracts_output/``). Path traversal is
    blocked. Files larger than 100 MB are rejected.

No subprocess. No eval. No dynamic code loading.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Per-process flag. Default off; tests + advanced users flip it on.
# This is a deliberate friction point: opting into local webhooks is a
# conscious choice, not a default behavior.
ALLOW_LOCAL_WEBHOOKS = bool(
    os.environ.get("AURORA_ALLOW_LOCAL_WEBHOOKS") == "1"
)

# Where file-actions may write. Outside this root → reject.
DEFAULT_CONTRACTS_OUTPUT_ROOT = Path(
    os.environ.get("AURORA_CONTRACTS_OUTPUT")
    or Path.home() / ".aurora" / "contracts_output"
)

# Request budget caps.
_WEBHOOK_TIMEOUT_DEFAULT_S = 10.0
_WEBHOOK_TIMEOUT_MAX_S = 30.0
_WEBHOOK_BODY_MAX_BYTES = 1 * 1024 * 1024  # 1 MB
_FILE_MAX_BYTES = 100 * 1024 * 1024  # 100 MB

_LOGGER = logging.getLogger("aurora.decision_contracts")


class InvalidActionError(ValueError):
    """Raised when an action document can't be built into a valid runner."""


class WebhookSecurityError(RuntimeError):
    """Raised when a webhook target violates the security policy."""


# ---------------------------------------------------------------------------
# Base + factory
# ---------------------------------------------------------------------------

class Action(ABC):
    """Base for all action runners."""

    @abstractmethod
    def run(self, ctx: Dict[str, Any]) -> None:
        """Execute the action with the given firing context. Must raise
        on failure (the engine catches and records the error string)."""

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """Round-trip serialisation."""


def build_action(d: Dict[str, Any]) -> Action:
    """Factory — pick the right Action subclass for an action document."""
    if not isinstance(d, dict):
        raise InvalidActionError("action must be a dict")
    kind = str(d.get("type") or "").lower()
    if kind == "webhook":
        return WebhookAction.from_dict(d)
    if kind == "log":
        return LogAction.from_dict(d)
    if kind == "file":
        return FileAction.from_dict(d)
    raise InvalidActionError(
        f"unsupported action type {kind!r}; supported: webhook | log | file"
    )


# ---------------------------------------------------------------------------
# WebhookAction
# ---------------------------------------------------------------------------

def _is_private_or_local(host: str) -> bool:
    """Return True when ``host`` resolves to a private / loopback /
    link-local / multicast address — the classic SSRF surface."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Unresolvable → treat as suspicious; refuse rather than fall
        # through to a request that might be retried later.
        return True
    for fam, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return True
    return False


def _redact_auth(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of ``headers`` with auth values redacted to the
    first 4 chars + ``…``. Never logs full tokens / passwords."""
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in ("authorization", "x-api-key", "cookie", "set-cookie"):
            sv = str(v)
            out[k] = (sv[:4] + "…[redacted]") if sv else "[redacted]"
        else:
            out[k] = v
    return out


@dataclass
class WebhookAction(Action):
    """HTTP POST a JSON payload to a webhook endpoint."""
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    timeout_s: float = _WEBHOOK_TIMEOUT_DEFAULT_S
    method: str = "POST"   # "POST" | "PUT"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WebhookAction":
        url = d.get("url")
        if not isinstance(url, str) or not url:
            raise InvalidActionError("webhook.url is required")
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise InvalidActionError(
                f"webhook.url must be http(s); got {parsed.scheme!r}"
            )
        if not parsed.hostname:
            raise InvalidActionError("webhook.url must include a hostname")
        # Headers must be a flat dict of strings.
        headers = d.get("headers") or {}
        if not isinstance(headers, dict):
            raise InvalidActionError("webhook.headers must be a dict")
        flat_headers = {}
        for k, v in headers.items():
            if not isinstance(k, str) or not isinstance(v, (str, int, float)):
                raise InvalidActionError("webhook.headers must be str→str")
            if not re.match(r"^[A-Za-z0-9_\-]+$", k):
                raise InvalidActionError(
                    f"webhook.headers key {k!r} has invalid characters"
                )
            flat_headers[k] = str(v)
        method = str(d.get("method") or "POST").upper()
        if method not in ("POST", "PUT"):
            raise InvalidActionError("webhook.method must be POST or PUT")
        try:
            t = float(d.get("timeout_s") or _WEBHOOK_TIMEOUT_DEFAULT_S)
        except (TypeError, ValueError):
            t = _WEBHOOK_TIMEOUT_DEFAULT_S
        t = min(max(0.5, t), _WEBHOOK_TIMEOUT_MAX_S)
        return cls(url=url, headers=flat_headers, timeout_s=t, method=method)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "webhook",
            "url": self.url,
            "headers": _redact_auth(self.headers),
            "timeout_s": self.timeout_s,
            "method": self.method,
        }

    def _check_target(self) -> None:
        """SSRF guard. Re-runs on every ``run()`` so that DNS
        re-resolution (e.g., for rotating endpoints) is honoured."""
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        # Direct-IP webhooks: must still pass the private-IP check.
        try:
            ipaddress.ip_address(host)
            ip_target = host
        except ValueError:
            ip_target = host
        if not ALLOW_LOCAL_WEBHOOKS and _is_private_or_local(ip_target):
            raise WebhookSecurityError(
                f"webhook target {host!r} resolves to a private / loopback "
                f"address; set ALLOW_LOCAL_WEBHOOKS=True (or environment "
                f"AURORA_ALLOW_LOCAL_WEBHOOKS=1) to permit"
            )

    def run(self, ctx: Dict[str, Any]) -> None:
        self._check_target()
        body = json.dumps({
            "contract_id": ctx.get("contract_id"),
            "contract_name": ctx.get("contract_name"),
            "trigger_field": ctx.get("trigger_field"),
            "trigger_value": ctx.get("trigger_value"),
            "bundle_run_id": ctx.get("bundle_run_id"),
            "bundle_hash": ctx.get("bundle_hash"),
            "metadata": ctx.get("metadata") or {},
            "fired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, ensure_ascii=False).encode("utf-8")
        if len(body) > _WEBHOOK_BODY_MAX_BYTES:
            raise WebhookSecurityError(
                f"webhook body exceeds {_WEBHOOK_BODY_MAX_BYTES} bytes"
            )
        # Use stdlib urllib so we don't add a network dep. (requests
        # would be nicer; not worth the import surface.)
        import urllib.request
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "aurora-decision-contracts/1.0",
                     **self.headers},
            method=self.method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                # Read up to 64 KB of response to keep audit log small.
                resp.read(64 * 1024)
        except Exception as e:
            # Re-raise with the URL stripped of any query-string secrets.
            sanitized = self.url.split("?", 1)[0]
            raise RuntimeError(
                f"webhook POST to {sanitized} failed: {type(e).__name__}: {e}"
            )


# ---------------------------------------------------------------------------
# LogAction
# ---------------------------------------------------------------------------

@dataclass
class LogAction(Action):
    """Write a structured log line via Python ``logging``."""
    level: str = "info"
    message: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LogAction":
        lvl = str(d.get("level") or "info").lower()
        if lvl not in ("debug", "info", "warn", "warning", "error", "critical"):
            raise InvalidActionError(f"log.level invalid: {lvl}")
        if lvl == "warn":
            lvl = "warning"
        msg = str(d.get("message") or "")
        return cls(level=lvl, message=msg)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "log", "level": self.level, "message": self.message}

    def run(self, ctx: Dict[str, Any]) -> None:
        fn = getattr(_LOGGER, self.level)
        try:
            fn(
                "[decision-contract] %s — %s (trigger=%s value=%s run=%s)",
                ctx.get("contract_id"),
                self.message or ctx.get("contract_name"),
                ctx.get("trigger_field"),
                ctx.get("trigger_value"),
                ctx.get("bundle_run_id"),
            )
        except Exception:
            # Logger failure should never break a firing.
            pass


# ---------------------------------------------------------------------------
# FileAction
# ---------------------------------------------------------------------------

@dataclass
class FileAction(Action):
    """Append a JSON line to a file under ``AURORA_CONTRACTS_OUTPUT``."""
    relative_path: str
    # Whether to write only the firing context (default) or include the
    # full bundle hash + timestamp as the JSON payload.
    include_metadata: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FileAction":
        rp = d.get("path") or d.get("relative_path")
        if not isinstance(rp, str) or not rp:
            raise InvalidActionError("file.path is required (relative string)")
        # Block traversal up front. ``..`` is rejected; absolute paths
        # too (caller must use AURORA_CONTRACTS_OUTPUT to set the root).
        if rp.startswith("/") or rp.startswith("\\"):
            raise InvalidActionError("file.path must be relative")
        if ".." in Path(rp).parts:
            raise InvalidActionError("file.path may not contain '..'")
        return cls(relative_path=rp,
                    include_metadata=bool(d.get("include_metadata", True)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "file",
            "path": self.relative_path,
            "include_metadata": self.include_metadata,
        }

    def _resolved(self) -> Path:
        root = Path(DEFAULT_CONTRACTS_OUTPUT_ROOT).expanduser().resolve()
        target = (root / self.relative_path).resolve()
        # Double-check after resolve() — symlinks etc. could still escape.
        try:
            target.relative_to(root)
        except ValueError:
            raise InvalidActionError(
                "resolved file path escapes the contracts-output root"
            )
        return target

    def run(self, ctx: Dict[str, Any]) -> None:
        target = self._resolved()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Cap file size — refuse to keep appending forever.
        if target.exists() and target.stat().st_size > _FILE_MAX_BYTES:
            raise RuntimeError(
                f"file {target} exceeds {_FILE_MAX_BYTES}-byte cap; rotate it"
            )
        rec = {
            "fired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "contract_id": ctx.get("contract_id"),
            "contract_name": ctx.get("contract_name"),
            "trigger_field": ctx.get("trigger_field"),
            "trigger_value": ctx.get("trigger_value"),
        }
        if self.include_metadata:
            rec["bundle_run_id"] = ctx.get("bundle_run_id")
            rec["bundle_hash"] = ctx.get("bundle_hash")
            rec["metadata"] = ctx.get("metadata") or {}
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

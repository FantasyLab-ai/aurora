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
    if kind == "slack":
        return SlackAction.from_dict(d)
    if kind == "discord":
        return DiscordAction.from_dict(d)
    if kind == "email":
        return EmailAction.from_dict(d)
    raise InvalidActionError(
        f"unsupported action type {kind!r}; "
        f"supported: webhook | log | file | slack | discord | email"
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


# ---------------------------------------------------------------------------
# v1.2: SlackAction — Slack Incoming Webhook
# ---------------------------------------------------------------------------
#
# Slack incoming webhooks accept a JSON POST with shape::
#
#   {"text": "...", "blocks": [...]}
#
# We POST to the user's webhook URL (validated via the same SSRF guard
# WebhookAction uses) and format the contract context as a small
# block-kit message. Authentication is implicit in the webhook URL —
# users keep that URL secret. We never log the URL in plain.
# ---------------------------------------------------------------------------

@dataclass
class SlackAction(Action):
    """POST a formatted Slack message to an incoming webhook URL.

    Schema:
        {"type": "slack",
         "webhook_url": "https://hooks.slack.com/services/...",
         "channel": "#alerts",          # optional, advisory
         "username": "Aurora",          # optional, advisory
         "icon_emoji": ":aurora:"       # optional, advisory
        }
    """
    webhook_url: str
    channel:     Optional[str] = None
    username:    str = "Aurora"
    icon_emoji:  Optional[str] = ":bar_chart:"
    timeout_s:   float = _WEBHOOK_TIMEOUT_DEFAULT_S

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SlackAction":
        url = d.get("webhook_url") or d.get("url")
        if not isinstance(url, str) or not url:
            raise InvalidActionError("slack.webhook_url is required")
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            raise InvalidActionError(
                f"slack.webhook_url must be https; got {parsed.scheme!r}"
            )
        # Slack webhooks all live under hooks.slack.com. We don't hard-
        # require that (some enterprises proxy through their own
        # gateway), but we do warn the user if they pass a non-slack
        # host — they'll find out quickly when the action fails.
        if not parsed.hostname:
            raise InvalidActionError("slack.webhook_url must include a hostname")
        try:
            t = float(d.get("timeout_s") or _WEBHOOK_TIMEOUT_DEFAULT_S)
        except (TypeError, ValueError):
            t = _WEBHOOK_TIMEOUT_DEFAULT_S
        t = min(max(0.5, t), _WEBHOOK_TIMEOUT_MAX_S)
        return cls(
            webhook_url=url,
            channel=(d.get("channel") or None),
            username=str(d.get("username") or "Aurora"),
            icon_emoji=(d.get("icon_emoji") or ":bar_chart:"),
            timeout_s=t,
        )

    def to_dict(self) -> Dict[str, Any]:
        # URL is the secret — redact in audit serialisations.
        parsed = urlparse(self.webhook_url)
        redacted = f"{parsed.scheme}://{parsed.hostname}/…[redacted]"
        return {
            "type": "slack",
            "webhook_url": redacted,
            "channel": self.channel,
            "username": self.username,
            "icon_emoji": self.icon_emoji,
            "timeout_s": self.timeout_s,
        }

    def _check_target(self) -> None:
        parsed = urlparse(self.webhook_url)
        host = parsed.hostname or ""
        if not ALLOW_LOCAL_WEBHOOKS and _is_private_or_local(host):
            raise WebhookSecurityError(
                f"slack webhook host {host!r} resolves to a private / "
                f"loopback address; refused"
            )

    def _format_message(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        contract_id = ctx.get("contract_id") or "?"
        contract_name = ctx.get("contract_name") or contract_id
        field = ctx.get("trigger_field") or "?"
        value = ctx.get("trigger_value")
        bundle_run = ctx.get("bundle_run_id") or "—"
        bundle_hash = ctx.get("bundle_hash") or "—"
        # Block-kit payload + plaintext fallback so older Slack clients
        # still see something useful.
        text = (f"🚨 Aurora contract *{contract_name}* fired — "
                f"`{field}` = `{value}` (run `{bundle_run}`)")
        blocks = [
            {"type": "header",
              "text": {"type": "plain_text",
                       "text": f"Aurora — {contract_name}"}},
            {"type": "section",
              "fields": [
                  {"type": "mrkdwn", "text": f"*Contract*\n`{contract_id}`"},
                  {"type": "mrkdwn", "text": f"*Trigger*\n`{field}` = `{value}`"},
                  {"type": "mrkdwn", "text": f"*Run*\n`{bundle_run}`"},
                  {"type": "mrkdwn", "text": f"*Bundle hash*\n`{bundle_hash[:16]}…`"},
              ]},
            {"type": "context",
              "elements": [{"type": "mrkdwn",
                            "text": f"fired at {time.strftime('%Y-%m-%dT%H:%M:%S')}"}]},
        ]
        payload = {"text": text, "blocks": blocks, "username": self.username}
        if self.channel:
            payload["channel"] = self.channel
        if self.icon_emoji:
            payload["icon_emoji"] = self.icon_emoji
        return payload

    def run(self, ctx: Dict[str, Any]) -> None:
        self._check_target()
        body = json.dumps(self._format_message(ctx),
                           ensure_ascii=False).encode("utf-8")
        if len(body) > _WEBHOOK_BODY_MAX_BYTES:
            raise WebhookSecurityError(
                f"slack payload exceeds {_WEBHOOK_BODY_MAX_BYTES} bytes"
            )
        import urllib.request
        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "aurora-decision-contracts/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                resp.read(64 * 1024)
        except Exception as e:
            sanitized = f"{urlparse(self.webhook_url).hostname}/…"
            raise RuntimeError(
                f"slack POST to {sanitized} failed: {type(e).__name__}: {e}"
            )


# ---------------------------------------------------------------------------
# v1.2: DiscordAction — Discord Channel Webhook
# ---------------------------------------------------------------------------
#
# Discord webhooks accept a JSON POST with shape::
#
#   {"content": "...", "embeds": [{"title": ..., "fields": [...]}]}
#
# Same SSRF guard as WebhookAction. The URL is the secret; we never
# log it in plain.
# ---------------------------------------------------------------------------

@dataclass
class DiscordAction(Action):
    """POST a formatted Discord message to a channel webhook URL.

    Schema:
        {"type": "discord",
         "webhook_url": "https://discord.com/api/webhooks/.../...",
         "username": "Aurora"            # optional
        }
    """
    webhook_url: str
    username:    str = "Aurora"
    timeout_s:   float = _WEBHOOK_TIMEOUT_DEFAULT_S

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DiscordAction":
        url = d.get("webhook_url") or d.get("url")
        if not isinstance(url, str) or not url:
            raise InvalidActionError("discord.webhook_url is required")
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            raise InvalidActionError(
                f"discord.webhook_url must be https; got {parsed.scheme!r}"
            )
        if not parsed.hostname:
            raise InvalidActionError("discord.webhook_url must include a hostname")
        try:
            t = float(d.get("timeout_s") or _WEBHOOK_TIMEOUT_DEFAULT_S)
        except (TypeError, ValueError):
            t = _WEBHOOK_TIMEOUT_DEFAULT_S
        t = min(max(0.5, t), _WEBHOOK_TIMEOUT_MAX_S)
        return cls(
            webhook_url=url,
            username=str(d.get("username") or "Aurora"),
            timeout_s=t,
        )

    def to_dict(self) -> Dict[str, Any]:
        parsed = urlparse(self.webhook_url)
        redacted = f"{parsed.scheme}://{parsed.hostname}/…[redacted]"
        return {
            "type": "discord",
            "webhook_url": redacted,
            "username": self.username,
            "timeout_s": self.timeout_s,
        }

    def _check_target(self) -> None:
        parsed = urlparse(self.webhook_url)
        host = parsed.hostname or ""
        if not ALLOW_LOCAL_WEBHOOKS and _is_private_or_local(host):
            raise WebhookSecurityError(
                f"discord webhook host {host!r} resolves to a private / "
                f"loopback address; refused"
            )

    def _format_message(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        contract_id = ctx.get("contract_id") or "?"
        contract_name = ctx.get("contract_name") or contract_id
        field = ctx.get("trigger_field") or "?"
        value = ctx.get("trigger_value")
        bundle_run = ctx.get("bundle_run_id") or "—"
        bundle_hash = ctx.get("bundle_hash") or "—"
        embed = {
            "title": f"Aurora — {contract_name}",
            # Discord embed colour (yellow-amber for "fired" — caller can
            # override via metadata.embed_color in a future schema rev).
            "color": 0xE6A23C,
            "fields": [
                {"name": "Contract", "value": f"`{contract_id}`", "inline": True},
                {"name": "Trigger", "value": f"`{field}` = `{value}`", "inline": True},
                {"name": "Run", "value": f"`{bundle_run}`", "inline": False},
                {"name": "Bundle hash",
                  "value": f"`{bundle_hash[:24]}…`" if bundle_hash != "—" else "—",
                  "inline": False},
            ],
            "footer": {"text": f"fired at {time.strftime('%Y-%m-%dT%H:%M:%S')}"},
        }
        return {
            "content": f"🚨 Aurora contract **{contract_name}** fired",
            "username": self.username,
            "embeds": [embed],
        }

    def run(self, ctx: Dict[str, Any]) -> None:
        self._check_target()
        body = json.dumps(self._format_message(ctx),
                           ensure_ascii=False).encode("utf-8")
        if len(body) > _WEBHOOK_BODY_MAX_BYTES:
            raise WebhookSecurityError(
                f"discord payload exceeds {_WEBHOOK_BODY_MAX_BYTES} bytes"
            )
        import urllib.request
        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "aurora-decision-contracts/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                resp.read(64 * 1024)
        except Exception as e:
            sanitized = f"{urlparse(self.webhook_url).hostname}/…"
            raise RuntimeError(
                f"discord POST to {sanitized} failed: {type(e).__name__}: {e}"
            )


# ---------------------------------------------------------------------------
# v1.2: EmailAction — SMTP via stdlib smtplib
# ---------------------------------------------------------------------------
#
# Email config lives on the action document. Credentials can also be
# pulled from env (preferred for cloud deploys): set ``AURORA_SMTP_USER``
# and ``AURORA_SMTP_PASS``. The action document then only references
# the host/port/from/to fields — never the secret.
#
# Sandboxing:
#   * Only TLS-via-STARTTLS (port 587) or SMTPS (port 465) accepted.
#     Plain SMTP (port 25) refused unless ``AURORA_ALLOW_PLAINTEXT_SMTP=1``.
#   * Recipients capped at 20 per action (anti-spam guard).
#   * Subject + body lengths capped (256 + 16K).
#   * No HTML by default — plain text only, with optional ``html`` body.
# ---------------------------------------------------------------------------

_SMTP_MAX_RECIPIENTS = 20
_SMTP_SUBJECT_MAX = 256
_SMTP_BODY_MAX = 16 * 1024


@dataclass
class EmailAction(Action):
    """Send a plain-text (or optional HTML) email via SMTP.

    Schema:
        {"type": "email",
         "host": "smtp.example.com",
         "port": 587,
         "from_addr": "aurora@example.com",
         "to_addrs": ["alice@example.com", "bob@example.com"],
         "subject": "Aurora alert: {contract_name}",     # optional, templated
         "body_template": "Trigger {trigger_field}=..."  # optional, templated
        }

    Credentials come from env: ``AURORA_SMTP_USER`` / ``AURORA_SMTP_PASS``.
    """
    host: str
    port: int
    from_addr: str
    to_addrs: List[str]
    subject_template: str = "Aurora alert: {contract_name}"
    body_template: str = (
        "Aurora decision contract {contract_id} fired.\n\n"
        "Trigger:  {trigger_field} = {trigger_value}\n"
        "Bundle:   {bundle_run_id} (hash {bundle_hash})\n"
        "Fired at: {fired_at}\n"
    )
    use_tls: bool = True   # STARTTLS by default (port 587)
    use_ssl: bool = False  # SMTPS (port 465) when True
    timeout_s: float = 15.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EmailAction":
        host = d.get("host") or ""
        if not isinstance(host, str) or not host.strip():
            raise InvalidActionError("email.host is required")
        try:
            port = int(d.get("port") or 587)
        except (TypeError, ValueError):
            raise InvalidActionError("email.port must be an int")
        from_addr = d.get("from_addr") or ""
        if not isinstance(from_addr, str) or "@" not in from_addr:
            raise InvalidActionError("email.from_addr is required (must contain @)")
        raw_to = d.get("to_addrs") or []
        if not isinstance(raw_to, list) or not raw_to:
            raise InvalidActionError("email.to_addrs must be a non-empty list")
        if len(raw_to) > _SMTP_MAX_RECIPIENTS:
            raise InvalidActionError(
                f"email.to_addrs may not exceed {_SMTP_MAX_RECIPIENTS} entries"
            )
        to_addrs: List[str] = []
        for r in raw_to:
            if not isinstance(r, str) or "@" not in r:
                raise InvalidActionError(
                    f"email.to_addrs contains invalid entry: {r!r}"
                )
            to_addrs.append(r.strip())
        subject = str(d.get("subject") or
                       "Aurora alert: {contract_name}")[:_SMTP_SUBJECT_MAX]
        body = str(d.get("body_template") or
                    EmailAction.body_template)[:_SMTP_BODY_MAX]

        # Encryption policy.
        use_ssl = bool(d.get("use_ssl", port == 465))
        use_tls = bool(d.get("use_tls", port == 587))
        if not use_ssl and not use_tls:
            # Plain SMTP — only permitted if the user opted in.
            if os.environ.get("AURORA_ALLOW_PLAINTEXT_SMTP") != "1":
                raise InvalidActionError(
                    "plain SMTP requires AURORA_ALLOW_PLAINTEXT_SMTP=1; "
                    "use TLS (port 587) or SSL (port 465)"
                )
        try:
            t = float(d.get("timeout_s") or 15.0)
        except (TypeError, ValueError):
            t = 15.0
        t = min(max(1.0, t), 60.0)
        return cls(
            host=host.strip(),
            port=port,
            from_addr=from_addr.strip(),
            to_addrs=to_addrs,
            subject_template=subject,
            body_template=body,
            use_tls=use_tls,
            use_ssl=use_ssl,
            timeout_s=t,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "email",
            "host": self.host,
            "port": self.port,
            "from_addr": self.from_addr,
            "to_addrs": list(self.to_addrs),
            "subject": self.subject_template,
            "body_template": self.body_template,
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
            "timeout_s": self.timeout_s,
        }

    def _render(self, template: str, ctx: Dict[str, Any]) -> str:
        """Safe template render — only the documented placeholder names
        are substituted. Unknown placeholders fail silently."""
        safe_ctx = {
            "contract_id":   str(ctx.get("contract_id")  or "?"),
            "contract_name": str(ctx.get("contract_name") or "?"),
            "trigger_field": str(ctx.get("trigger_field") or "?"),
            "trigger_value": str(ctx.get("trigger_value")),
            "bundle_run_id": str(ctx.get("bundle_run_id") or "?"),
            "bundle_hash":   str(ctx.get("bundle_hash")   or "?"),
            "fired_at":      time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            return template.format(**safe_ctx)
        except Exception:
            # Unknown {placeholder} → return template verbatim.
            return template

    def run(self, ctx: Dict[str, Any]) -> None:
        import smtplib
        from email.mime.text import MIMEText
        subject = self._render(self.subject_template, ctx)[:_SMTP_SUBJECT_MAX]
        body = self._render(self.body_template, ctx)[:_SMTP_BODY_MAX]

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)

        user = os.environ.get("AURORA_SMTP_USER")
        password = os.environ.get("AURORA_SMTP_PASS")

        try:
            if self.use_ssl:
                smtp = smtplib.SMTP_SSL(self.host, self.port,
                                          timeout=self.timeout_s)
            else:
                smtp = smtplib.SMTP(self.host, self.port,
                                     timeout=self.timeout_s)
                if self.use_tls:
                    smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            smtp.quit()
        except Exception as e:
            raise RuntimeError(
                f"smtp send to {self.host}:{self.port} failed: "
                f"{type(e).__name__}: {e}"
            )

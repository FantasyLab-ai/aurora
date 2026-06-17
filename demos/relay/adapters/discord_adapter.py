"""
Discord adapter — turn an Aurora fire envelope into a Discord embed.

We POST to a Discord Incoming Webhook URL (you create one in any
Discord channel's settings → Integrations → Webhooks). The URL is the
secret — never log it in plain, never paste it into the contract JSON
directly. Set `AURORA_DEMO_DISCORD_WEBHOOK=...` instead and the relay
picks it up from env at startup.

Why we do this in the relay (not Aurora's native DiscordAction):

   Native action exists in v1.2, but using the generic webhook → relay
   means the same Aurora contract can drive Discord + Slack + the
   overlay + a smart plug at once. The relay is the fan-out point;
   Aurora stays simple.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict
from urllib import request as _urlreq
from urllib.parse import urlparse


def post(envelope: Dict[str, Any], webhook_url: str,
          *, timeout_s: float = 6.0) -> Dict[str, Any]:
    """POST an Aurora fire envelope to Discord. Returns a result dict."""
    if not webhook_url:
        return {"ok": False, "error": "no Discord webhook URL configured"}

    try:
        payload = _format(envelope)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _urlreq.Request(
            webhook_url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "aurora-demo-relay/1.0",
            },
        )
        with _urlreq.urlopen(req, timeout=timeout_s) as resp:
            return {
                "ok": resp.status < 300,
                "status": resp.status,
                # Don't log the full URL — it's the secret. Just the host.
                "target": urlparse(webhook_url).hostname,
            }
    except Exception as e:
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}",
                "target": urlparse(webhook_url).hostname or "?"}


def _format(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Discord embed payload from the envelope."""
    sev = (envelope.get("severity") or "info").lower()
    sev_color = {
        "crit": 0xE74C5F,    # crimson
        "warn": 0xE6A23C,    # amber
        "info": 0x6EE7FF,    # cyan
        "ok":   0x88FFD1,    # mint
    }.get(sev, 0x9C9C9C)

    contract_name = envelope.get("contract_name") or envelope.get("contract_id") or "(unnamed contract)"
    field         = envelope.get("trigger_field") or "?"
    value         = envelope.get("trigger_value")
    method        = envelope.get("method") or "—"
    row           = envelope.get("row")
    z             = envelope.get("z_score")
    fabricated    = int(envelope.get("fabricated") or 0)
    run_id        = envelope.get("bundle_run_id") or "—"
    bundle_hash   = envelope.get("bundle_hash") or "—"

    # Title + content — the visible message in the channel.
    content = f"🚨 **Aurora · {contract_name}** fired"

    # Embed fields — the "citation" — this is the visual climax in-channel.
    fields = [
        {"name": "Trigger", "value": f"`{field}` = `{value}`", "inline": True},
        {"name": "Severity", "value": f"`{sev.upper()}`", "inline": True},
        {"name": "Method",   "value": f"`{method}`",        "inline": True},
    ]
    if row is not None:
        fields.append({"name": "Row", "value": f"`{row}`", "inline": True})
    if z is not None:
        fields.append({"name": "|z|", "value": f"`{abs(float(z)):.2f}σ`", "inline": True})
    fields.append({"name": "fabricated",
                    "value": f"`{fabricated}` ✓" if fabricated == 0 else f"`{fabricated}` ⚠",
                    "inline": True})
    if run_id != "—":
        fields.append({"name": "Run", "value": f"`{run_id}`", "inline": False})
    if bundle_hash and bundle_hash != "—":
        # Short hash for the footer-style display.
        fields.append({"name": "Bundle hash",
                        "value": f"`{str(bundle_hash)[:24]}…`",
                        "inline": False})

    embed = {
        "title":  f"Aurora · {sev.upper()}",
        "color":  sev_color,
        "fields": fields,
        "footer": {"text": f"aurora-cortex · fired {time.strftime('%H:%M:%S')}"},
    }

    return {
        "username": "Aurora Sentinel",
        "content":  content,
        "embeds":   [embed],
    }

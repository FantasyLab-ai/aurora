"""
Slack adapter — turn an Aurora fire envelope into a Slack Block-Kit
message and POST it to an Incoming Webhook URL.

URL is the secret. Set `AURORA_DEMO_SLACK_WEBHOOK=...` env var.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict
from urllib import request as _urlreq
from urllib.parse import urlparse


def post(envelope: Dict[str, Any], webhook_url: str,
          *, timeout_s: float = 6.0) -> Dict[str, Any]:
    """POST an Aurora fire envelope to Slack."""
    if not webhook_url:
        return {"ok": False, "error": "no Slack webhook URL configured"}
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
            return {"ok": resp.status < 300,
                    "status": resp.status,
                    "target": urlparse(webhook_url).hostname}
    except Exception as e:
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}",
                "target": urlparse(webhook_url).hostname or "?"}


def _format(envelope: Dict[str, Any]) -> Dict[str, Any]:
    sev = (envelope.get("severity") or "info").lower()
    contract_name = envelope.get("contract_name") or envelope.get("contract_id") or "(unnamed)"
    field         = envelope.get("trigger_field") or "?"
    value         = envelope.get("trigger_value")
    method        = envelope.get("method") or "—"
    row           = envelope.get("row")
    z             = envelope.get("z_score")
    fabricated    = int(envelope.get("fabricated") or 0)
    run_id        = envelope.get("bundle_run_id") or "—"

    text = f":rotating_light: *Aurora · {contract_name}* fired — `{field}` = `{value}`"

    fields = [
        {"type": "mrkdwn", "text": f"*Method*\n`{method}`"},
        {"type": "mrkdwn", "text": f"*Severity*\n`{sev.upper()}`"},
    ]
    if row is not None:
        fields.append({"type": "mrkdwn", "text": f"*Row*\n`{row}`"})
    if z is not None:
        fields.append({"type": "mrkdwn", "text": f"*|z|*\n`{abs(float(z)):.2f}σ`"})
    fields.append({"type": "mrkdwn",
                    "text": f"*Fabricated*\n`{fabricated}` " +
                            (":white_check_mark:" if fabricated == 0 else ":warning:")})
    fields.append({"type": "mrkdwn", "text": f"*Run*\n`{run_id}`"})

    blocks = [
        {"type": "header",
          "text": {"type": "plain_text", "text": f"Aurora · {sev.upper()}"}},
        {"type": "section", "fields": fields},
        {"type": "context",
          "elements": [
              {"type": "mrkdwn",
                "text": f"aurora-cortex · fired {time.strftime('%H:%M:%S')}"},
          ]},
    ]
    return {"text": text, "blocks": blocks, "username": "Aurora Sentinel"}

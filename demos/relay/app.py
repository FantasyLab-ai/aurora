"""
Aurora demo relay — fan-out webhook receiver.

Aurora's Decision Contracts ship a generic ``webhook`` action that
POSTs a JSON envelope to a single URL. For the demo videos we want
that one signal to drive multiple targets:

  * Discord (rich embed)
  * Slack (formatted message)
  * OBS overlay (Server-Sent Events → on-screen "Aurora fired" card)
  * Log file (the "save" demo — proof it caught what humans missed)
  * Device adapter (smart plug / LED / servo — the "alarm" demo)

This relay sits between Aurora and the world. Aurora hits the relay's
``/aurora/fire`` endpoint; the relay parses the payload and pushes
to every configured adapter.

Run:

    python demos/relay/app.py            # default port 7077

Aurora's contract action then targets ``http://127.0.0.1:7077/aurora/fire``.

This unblocks the demos TODAY without waiting for any further
contract-action types to ship — the relay translates the generic
webhook into whatever per-demo shape we need.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from flask import Flask, Response, jsonify, request, send_from_directory
except ImportError:  # pragma: no cover
    raise SystemExit(
        "flask is required: pip install flask  "
        "(should already be installed for Aurora Studio)"
    )

from .adapters import (
    discord_adapter,
    slack_adapter,
    log_adapter,
    sse_adapter,
    device_adapter,
)


# ---------------------------------------------------------------------------
# Configuration — all overrides via env so demos don't hard-code secrets.
# ---------------------------------------------------------------------------

CONFIG = {
    "discord_webhook_url": os.environ.get("AURORA_DEMO_DISCORD_WEBHOOK", "").strip(),
    "slack_webhook_url":   os.environ.get("AURORA_DEMO_SLACK_WEBHOOK", "").strip(),
    "log_file_path":       os.environ.get("AURORA_DEMO_LOG_PATH",
                                            "demos/relay/fires.jsonl"),
    "device_url":          os.environ.get("AURORA_DEMO_DEVICE_URL", "").strip(),
    "device_kind":         os.environ.get("AURORA_DEMO_DEVICE_KIND", "esp32"),
    # Comma-separated list of which adapters to invoke. Default: all
    # available (skip ones with empty config). Override for per-demo
    # focus: AURORA_DEMO_ADAPTERS=discord,sse,log
    "enabled_adapters":    os.environ.get("AURORA_DEMO_ADAPTERS", "auto"),
    "port":                int(os.environ.get("AURORA_DEMO_PORT", "7077")),
}

# Bounded queue per subscriber for the SSE overlay stream.
_SSE_QUEUES: List["queue.Queue[Dict[str, Any]]"] = []
_SSE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return jsonify({
        "ok": True,
        "service": "aurora-demo-relay",
        "endpoints": {
            "fire":     "/aurora/fire   (POST)  — Aurora's contract webhook",
            "events":   "/aurora/events (GET SSE) — overlay listens here",
            "status":   "/aurora/status (GET)",
            "overlay":  "/overlay/      (GET)  — the on-screen card",
        },
        "config_summary": {
            "discord":  "configured" if CONFIG["discord_webhook_url"] else "missing AURORA_DEMO_DISCORD_WEBHOOK",
            "slack":    "configured" if CONFIG["slack_webhook_url"]   else "missing AURORA_DEMO_SLACK_WEBHOOK",
            "log_file": CONFIG["log_file_path"],
            "device":   CONFIG["device_url"] or "no device configured",
            "enabled":  CONFIG["enabled_adapters"],
        },
    })


@app.route("/aurora/status")
def status():
    return jsonify({
        "ok": True,
        "service": "aurora-demo-relay",
        "subscribers": _subscriber_count(),
        "enabled_adapters": _resolve_enabled_adapters(),
    })


@app.route("/aurora/fire", methods=["POST"])
def fire():
    """The endpoint Aurora's Decision Contract webhook targets.

    Expected payload (matches WebhookAction.run() body shape):

        {
            "contract_id":    "...",
            "contract_name":  "...",
            "trigger_field":  "findings.crit_count",
            "trigger_value":  4,
            "bundle_run_id":  "...",
            "bundle_hash":    "...",
            "metadata":       {...},
            "fired_at":       "2026-..."
        }

    We don't validate the shape — anything POSTed gets translated into
    the demo overlay card + fanned out to the configured adapters.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400

    # Normalise into the "fire envelope" the adapters consume.
    envelope = {
        "received_at":   time.time(),
        "contract_id":   payload.get("contract_id") or payload.get("contract") or "(unknown)",
        "contract_name": payload.get("contract_name") or payload.get("name") or "(unnamed)",
        "trigger_field": payload.get("trigger_field"),
        "trigger_value": payload.get("trigger_value"),
        "severity":      _infer_severity(payload),
        "method":        _infer_method(payload),
        "row":           _infer_row(payload),
        "z_score":       _infer_zscore(payload),
        "bundle_run_id": payload.get("bundle_run_id"),
        "bundle_hash":   payload.get("bundle_hash"),
        "fabricated":    int(payload.get("fabricated_count") or
                              (payload.get("metadata") or {}).get("fabricated_count") or 0),
        "raw":           payload,
    }

    results = _fan_out(envelope)

    return jsonify({
        "ok": True,
        "envelope": {k: v for k, v in envelope.items() if k != "raw"},
        "adapter_results": results,
    })


@app.route("/aurora/events")
def events():
    """Server-Sent Events stream. The overlay opens an EventSource here
    and re-renders its card each time a `fire` event arrives."""
    q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=64)
    with _SSE_LOCK:
        _SSE_QUEUES.append(q)

    def gen():
        try:
            # Heartbeat every 25s so reverse proxies don't reap us.
            last_heartbeat = time.time()
            while True:
                try:
                    msg = q.get(timeout=5)
                    data = json.dumps(msg, default=str, ensure_ascii=False)
                    yield f"event: {msg.get('kind', 'fire')}\ndata: {data}\n\n"
                except queue.Empty:
                    if time.time() - last_heartbeat > 25:
                        yield "event: heartbeat\ndata: {}\n\n"
                        last_heartbeat = time.time()
        finally:
            with _SSE_LOCK:
                if q in _SSE_QUEUES:
                    _SSE_QUEUES.remove(q)

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.route("/overlay/")
@app.route("/overlay/<path:filename>")
def overlay(filename: str = "index.html"):
    """Serve the OBS browser-source overlay assets.

    Sends `Cache-Control: no-store` on every asset. Without this,
    Chrome aggressively caches `app.js` + `style.css` (12h TTL from
    Flask's default `send_from_directory`), and changes we push
    here don't reach the overlay until the user manually hard-
    refreshes. For a recording rig where iteration is constant,
    that's a deal-breaker — bake it out.
    """
    from pathlib import Path
    overlay_dir = Path(__file__).resolve().parent.parent / "overlay"
    resp = send_from_directory(str(overlay_dir), filename)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _fan_out(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch the envelope to every enabled adapter."""
    enabled = _resolve_enabled_adapters()
    results: Dict[str, Any] = {}

    if "sse" in enabled:
        results["sse"] = _broadcast_sse(envelope)
    if "log" in enabled:
        results["log"] = log_adapter.append(envelope, CONFIG["log_file_path"])
    if "discord" in enabled and CONFIG["discord_webhook_url"]:
        results["discord"] = discord_adapter.post(
            envelope, CONFIG["discord_webhook_url"],
        )
    if "slack" in enabled and CONFIG["slack_webhook_url"]:
        results["slack"] = slack_adapter.post(
            envelope, CONFIG["slack_webhook_url"],
        )
    if "device" in enabled and CONFIG["device_url"]:
        results["device"] = device_adapter.trigger(
            envelope, CONFIG["device_url"], CONFIG["device_kind"],
        )

    return results


def _resolve_enabled_adapters() -> List[str]:
    raw = CONFIG["enabled_adapters"].strip().lower()
    if raw == "auto":
        # Everything that's configured.
        out = ["sse", "log"]
        if CONFIG["discord_webhook_url"]: out.append("discord")
        if CONFIG["slack_webhook_url"]:   out.append("slack")
        if CONFIG["device_url"]:          out.append("device")
        return out
    return [s.strip() for s in raw.split(",") if s.strip()]


def _broadcast_sse(envelope: Dict[str, Any]) -> Dict[str, Any]:
    msg = {"kind": "fire", **envelope}
    with _SSE_LOCK:
        n_sent = 0
        n_dropped = 0
        for q in _SSE_QUEUES:
            try:
                q.put_nowait(msg)
                n_sent += 1
            except queue.Full:
                n_dropped += 1
    return {"sent": n_sent, "dropped": n_dropped}


def _subscriber_count() -> int:
    with _SSE_LOCK:
        return len(_SSE_QUEUES)


def _infer_severity(payload: Dict[str, Any]) -> str:
    """Pull a severity string out of whatever Aurora sent."""
    field = str(payload.get("trigger_field") or "")
    if "crit" in field.lower():
        return "crit"
    if "warn" in field.lower():
        return "warn"
    meta = payload.get("metadata") or {}
    sev = meta.get("severity") if isinstance(meta, dict) else None
    return str(sev or "info").lower()


def _infer_method(payload: Dict[str, Any]) -> str:
    meta = payload.get("metadata") or {}
    return str((meta.get("method") if isinstance(meta, dict) else None)
                or payload.get("method") or "—")


def _infer_row(payload: Dict[str, Any]) -> Optional[int]:
    meta = payload.get("metadata") or {}
    for src in (meta if isinstance(meta, dict) else {}, payload):
        for k in ("row", "row_idx", "claim_row"):
            v = src.get(k)
            try:
                if v is not None:
                    return int(v)
            except (TypeError, ValueError):
                pass
    return None


def _infer_zscore(payload: Dict[str, Any]) -> Optional[float]:
    meta = payload.get("metadata") or {}
    for src in (meta if isinstance(meta, dict) else {}, payload):
        for k in ("z", "z_score", "zscore"):
            v = src.get(k)
            try:
                if v is not None:
                    return float(v)
            except (TypeError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    print(f"[aurora-demo-relay] listening on http://127.0.0.1:{CONFIG['port']}")
    print(f"  POST /aurora/fire      ← point your Decision Contract webhook here")
    print(f"  GET  /aurora/events    ← OBS browser source listens here (SSE)")
    print(f"  GET  /overlay/         ← the on-screen Aurora-fired card")
    print()
    print(f"  Adapters: {_resolve_enabled_adapters()}")
    print()
    app.run(host="127.0.0.1", port=CONFIG["port"], debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

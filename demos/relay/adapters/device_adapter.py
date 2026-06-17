"""
Device adapter — drive a physical thing in the room from an Aurora fire.

Two flavours supported, picked via env `AURORA_DEMO_DEVICE_KIND`:

  * **esp32** (default) — POST a tiny JSON envelope to the firmware's
    HTTP endpoint. The ESP32 sketch lives in `demos/firmware/esp32/`
    (build separately); it parses the JSON and drives a GPIO LED/servo.

  * **shelly** — POST to a local Shelly smart-plug's HTTP API. Cycles
    the plug ON for 5s, then OFF. Works with Shelly Gen1/Gen2 in
    LAN mode (no cloud account needed).

Both adapters are network-only, no GPIO library required. The relay
runs on your dev machine; the hardware lives on the same LAN.

If `AURORA_DEMO_DEVICE_URL` is empty, this adapter is skipped.
"""
from __future__ import annotations

import json
from typing import Any, Dict
from urllib import request as _urlreq
from urllib.parse import urlparse


def trigger(envelope: Dict[str, Any], device_url: str,
            kind: str = "esp32",
            *, timeout_s: float = 4.0) -> Dict[str, Any]:
    """Hit the device endpoint. Return a result dict; never raise."""
    if not device_url:
        return {"ok": False, "error": "no device URL configured"}

    parsed = urlparse(device_url)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"device URL must be http(s); got {parsed.scheme!r}"}

    try:
        if kind == "shelly":
            return _trigger_shelly(device_url, timeout_s=timeout_s)
        # Default ESP32 / generic HTTP firmware.
        payload = {
            "command":  "fire",
            "severity": envelope.get("severity"),
            "method":   envelope.get("method"),
            "row":      envelope.get("row"),
            "z_score":  envelope.get("z_score"),
            "duration_ms": _duration_for_severity(envelope.get("severity")),
        }
        body = json.dumps(payload).encode("utf-8")
        req = _urlreq.Request(
            device_url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "aurora-demo-relay/1.0",
            },
        )
        with _urlreq.urlopen(req, timeout=timeout_s) as resp:
            return {"ok": resp.status < 300,
                    "status": resp.status,
                    "kind": kind,
                    "target": parsed.hostname}
    except Exception as e:
        return {"ok": False,
                "error": f"{type(e).__name__}: {e}",
                "kind": kind,
                "target": parsed.hostname or "?"}


def _trigger_shelly(device_url: str, *, timeout_s: float) -> Dict[str, Any]:
    """Cycle a Shelly plug: ON, wait 5s, OFF."""
    # Gen1: /relay/0?turn=on  ·  Gen2: /rpc/Switch.Set?id=0&on=true
    # Try the Gen2 path first; fall back to Gen1.
    parsed = urlparse(device_url)
    base = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base += f":{parsed.port}"

    for on_path, off_path in [
        ("/rpc/Switch.Set?id=0&on=true", "/rpc/Switch.Set?id=0&on=false"),
        ("/relay/0?turn=on",             "/relay/0?turn=off"),
    ]:
        try:
            _urlreq.urlopen(base + on_path, timeout=timeout_s).read(256)
            # Don't actually sleep server-side — the relay would block.
            # The hardware demo's video timing should be set with the
            # OBS scene transition, not by sleeping here.
            return {"ok": True, "kind": "shelly", "target": parsed.hostname,
                    "path": on_path,
                    "note": "Plug turned ON. Use the OBS scene cue or "
                             "a follow-up POST to /off to cycle it."}
        except Exception:
            continue
    return {"ok": False, "kind": "shelly",
            "error": "neither Gen1 nor Gen2 endpoint responded"}


def _duration_for_severity(sev: Any) -> int:
    """How long the LED / servo should hold (ms). Critical = 8s, warn = 4s."""
    s = str(sev or "info").lower()
    return {"crit": 8000, "warn": 4000, "info": 1500, "ok": 1000}.get(s, 1500)

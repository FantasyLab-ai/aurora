"""
Shared HTTP helper for the LLM provider backends.

Why ``urllib`` and not ``requests``: Aurora's runtime-deps rule says
new wheels need real justification. The HTTP behaviour each provider
needs is straightforward (POST JSON, parse JSON response, propagate
HTTP errors) and stdlib ``urllib`` is sufficient.

If we ever need streaming responses, retries with backoff, or
connection pooling, we'd revisit.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .base import LLMCallError


DEFAULT_TIMEOUT_S = 120.0


def post_json(
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Tuple[int, Dict[str, Any]]:
    """POST a JSON payload, return (status_code, parsed_response).
    Raises ``LLMCallError`` with a human message on network failure
    or non-JSON response. Non-2xx status codes are returned (not raised)
    so the caller can decide whether to retry or surface."""
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # HTTP error — read the body so callers can show provider's error.
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, _parse_or_wrap(raw)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMCallError(
            f"network error calling {url}: {type(e).__name__}: {e}"
        ) from e
    return status, _parse_or_wrap(raw)


def _parse_or_wrap(raw: str) -> Dict[str, Any]:
    """Parse JSON; if it isn't JSON, wrap it as a `{"_raw": ...}` dict
    so callers always get a Mapping back."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw[:8000]}


def require_env(name: str, *, provider: str) -> str:
    """Read an env var that's a precondition for a provider. Raises
    a clean error mentioning the provider so the user knows which
    credential is missing."""
    import os
    val = os.environ.get(name, "").strip()
    if not val:
        raise _config_error(
            f"{provider} provider requires env var {name!r} but it's unset. "
            f"Set it in your environment, .env file, or docker-compose.yml."
        )
    return val


def _config_error(msg: str):
    """Defer the import to avoid a circular dependency at module load."""
    from .base import LLMConfigError
    return LLMConfigError(msg)

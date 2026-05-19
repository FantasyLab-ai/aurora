"""
Anthropic Claude provider.

Talks to ``https://api.anthropic.com/v1/messages`` with the user's
``ANTHROPIC_API_KEY``. No SDK dependency — just HTTP.

Default model: ``claude-sonnet-4-5``. Override with
``ANTHROPIC_MODEL``.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMResponse, LLMCallError
from ._http import post_json, require_env


ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self):
        self.api_key = require_env("ANTHROPIC_API_KEY", provider="anthropic")
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.base_url = os.environ.get(
            "ANTHROPIC_BASE_URL", "https://api.anthropic.com"
        ).rstrip("/")

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self.base_url}/v1/messages"
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        }

        t0 = time.perf_counter()
        status, resp = post_json(url, payload, headers=headers, timeout=120.0)
        latency = round(time.perf_counter() - t0, 4)

        if status >= 400:
            err = (resp or {}).get("error", resp)
            raise LLMCallError(f"Anthropic HTTP {status}: {err}")

        content = resp.get("content") or []
        text = "".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = resp.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=resp.get("model", self.model),
            usage={
                "input_tokens":  usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            },
            latency_s=latency,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": "configured",
            "model": self.model,
            "base_url": self.base_url,
            "billing": "user pays Anthropic directly per token",
            "credential_env": "ANTHROPIC_API_KEY",
        }

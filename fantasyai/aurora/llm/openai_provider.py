"""
OpenAI provider.

Uses the Chat Completions API at ``https://api.openai.com/v1/chat/completions``.
Default model: ``gpt-4o-mini``. Override with ``OPENAI_MODEL``.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMResponse, LLMCallError
from ._http import post_json, require_env


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self):
        self.api_key = require_env("OPENAI_API_KEY", provider="openai")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com"
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
        url = f"{self.base_url}/v1/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        t0 = time.perf_counter()
        status, resp = post_json(url, payload, headers=headers, timeout=120.0)
        latency = round(time.perf_counter() - t0, 4)

        if status >= 400:
            err = (resp or {}).get("error", resp)
            raise LLMCallError(f"OpenAI HTTP {status}: {err}")

        choices = resp.get("choices") or []
        if not choices:
            raise LLMCallError(f"OpenAI returned no choices: {resp}")
        msg = (choices[0].get("message") or {})
        text = str(msg.get("content", ""))
        usage = resp.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=resp.get("model", self.model),
            usage={
                "prompt_tokens":     usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens":      usage.get("total_tokens"),
            },
            latency_s=latency,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": "configured",
            "model": self.model,
            "base_url": self.base_url,
            "billing": "user pays OpenAI directly per token",
            "credential_env": "OPENAI_API_KEY",
        }

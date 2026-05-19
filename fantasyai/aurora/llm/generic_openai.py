"""
Generic OpenAI-compatible provider.

For any HTTP endpoint that speaks the OpenAI Chat Completions wire
format — Groq, OpenRouter, vLLM, LM Studio's local server, your own
fine-tuned model behind a custom proxy, etc.

Config:

  * ``OPENAI_COMPATIBLE_BASE_URL`` — required, e.g. ``https://api.groq.com/openai``
  * ``OPENAI_COMPATIBLE_API_KEY`` — required
  * ``OPENAI_COMPATIBLE_MODEL`` — defaults to ``mixtral-8x7b-32768``
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMResponse, LLMCallError
from ._http import post_json, require_env


class GenericOpenAIProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self):
        self.base_url = require_env(
            "OPENAI_COMPATIBLE_BASE_URL", provider="openai_compatible"
        ).rstrip("/")
        self.api_key = require_env(
            "OPENAI_COMPATIBLE_API_KEY", provider="openai_compatible"
        )
        self.model = os.environ.get(
            "OPENAI_COMPATIBLE_MODEL", "mixtral-8x7b-32768"
        )

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
            raise LLMCallError(f"{self.base_url} HTTP {status}: {resp}")

        choices = resp.get("choices") or []
        if not choices:
            raise LLMCallError(f"{self.base_url} returned no choices: {resp}")
        text = str((choices[0].get("message") or {}).get("content", ""))
        usage = resp.get("usage") or {}

        return LLMResponse(
            text=text,
            provider=self.name,
            model=resp.get("model", self.model),
            usage=usage,
            latency_s=latency,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": "configured",
            "model": self.model,
            "base_url": self.base_url,
            "credential_env": "OPENAI_COMPATIBLE_API_KEY",
            "billing": "user pays the OpenAI-compatible provider directly",
        }

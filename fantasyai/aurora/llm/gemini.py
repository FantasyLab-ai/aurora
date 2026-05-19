"""
Google Gemini provider.

Uses the Generative Language API:
``https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent``
with an API key as query param. Default model: ``gemini-1.5-flash``.
Override with ``GEMINI_MODEL``.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMResponse, LLMCallError
from ._http import post_json, require_env


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self):
        self.api_key = require_env("GEMINI_API_KEY", provider="gemini")
        self.model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
        self.base_url = os.environ.get(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
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
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"

        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        t0 = time.perf_counter()
        status, resp = post_json(url, payload, timeout=120.0)
        latency = round(time.perf_counter() - t0, 4)

        if status >= 400:
            err = (resp or {}).get("error", resp)
            raise LLMCallError(f"Gemini HTTP {status}: {err}")

        candidates = resp.get("candidates") or []
        if not candidates:
            raise LLMCallError(f"Gemini returned no candidates: {resp}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = resp.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            usage={
                "prompt_token_count":     usage.get("promptTokenCount"),
                "candidates_token_count": usage.get("candidatesTokenCount"),
                "total_token_count":      usage.get("totalTokenCount"),
            },
            latency_s=latency,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": "configured",
            "model": self.model,
            "base_url": self.base_url,
            "billing": "user pays Google directly per token",
            "credential_env": "GEMINI_API_KEY",
        }

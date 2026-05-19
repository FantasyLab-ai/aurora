"""
Ollama provider — local LLM (the v1.1 default).

Talks to a running Ollama server via its HTTP API. Default endpoint is
``http://localhost:11434``; override with ``OLLAMA_BASE_URL``. Default
model is ``gemma3:12b``; override with ``OLLAMA_MODEL``.

Aurora ships with this as the recommended default because it
preserves the local-first thesis: no API keys, no data leaves the
user's machine.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMResponse, LLMCallError
from ._http import post_json


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self):
        self.base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.environ.get("OLLAMA_MODEL", "gemma3:12b")
        # Ollama allows huge contexts; we cap defensively.
        self.num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> LLMResponse:
        url = f"{self.base_url}/api/generate"
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_tokens),
                "num_ctx": self.num_ctx,
            },
        }
        if system:
            payload["system"] = system

        t0 = time.perf_counter()
        status, resp = post_json(url, payload, timeout=180.0)
        latency = round(time.perf_counter() - t0, 4)

        if status >= 400 or not isinstance(resp, dict):
            raise LLMCallError(
                f"Ollama returned HTTP {status}: {resp}"
            )

        text = str(resp.get("response", ""))
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            usage={
                "eval_count": resp.get("eval_count"),
                "prompt_eval_count": resp.get("prompt_eval_count"),
                "total_duration_ns": resp.get("total_duration"),
            },
            latency_s=latency,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": "configured",
            "base_url": self.base_url,
            "model": self.model,
            "billing": "free (runs locally; no API costs)",
        }

"""
The ``none`` provider — synthesis disabled.

Returns an empty response with provider='none'. The synthesis layer
falls back to template-based narrative (the same code path used when
RAG fails to find any matching entries). Aurora's math still produces
findings; only the free-form paragraph is skipped.

This is the right default for Aurora Cloud: don't pretend to call an
LLM until the user has configured one.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .base import LLMProvider, LLMResponse


class NoneProvider(LLMProvider):
    """No-op provider. Returns empty text + clear signal that no
    synthesis was performed."""
    name = "none"

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(
            text="",
            provider=self.name,
            model="(none)",
            usage={"note": "synthesis disabled; configure AURORA_LLM_PROVIDER"},
            latency_s=0.0,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": "disabled",
            "message": (
                "Synthesis disabled — Aurora's math runs but no narrative "
                "paragraph is generated. Set AURORA_LLM_PROVIDER in the "
                "environment (ollama / anthropic / openai / gemini / "
                "openai_compatible) to enable."
            ),
        }

"""
Provider-agnostic LLM interface.

Every backend implements ``LLMProvider`` with one method,
``generate(prompt, system, temperature, max_tokens)``, returning an
``LLMResponse`` with the text + provider-specific metadata.

The interface is intentionally minimal — no streaming, no tools, no
function-calling. Aurora's synthesis layer wants one thing from the
LLM: paraphrase the structured findings into a cited paragraph. Keep
the surface tight.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class LLMConfigError(Exception):
    """Raised when an LLM provider can't be constructed because its
    credentials / endpoint are missing or malformed. Surfaces in the
    Studio's settings panel as a friendly error."""


class LLMCallError(Exception):
    """Raised when an LLM call fails after the provider was
    successfully constructed (network, rate limit, bad prompt, etc.)."""


@dataclass(frozen=True)
class LLMResponse:
    """The result of a successful generation.

    Attributes:
        text:        The generated text.
        provider:    Name of the provider that served the request
                     (e.g., 'ollama', 'anthropic').
        model:       Model identifier the provider used.
        usage:       Provider-specific token / cost dict. Best-effort —
                     not every provider returns this.
        latency_s:   Wall-clock time in seconds.
    """
    text: str
    provider: str
    model: str
    usage: Dict[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0


class LLMProvider:
    """Abstract base. Subclasses implement ``generate``.

    Concrete providers live in their own modules:

      * ``ollama.OllamaProvider``
      * ``anthropic.AnthropicProvider``
      * ``openai.OpenAIProvider``
      * ``gemini.GeminiProvider``
      * ``generic_openai.GenericOpenAIProvider``
    """

    #: Identifier used in logs + the Studio settings panel.
    name: str = "base"

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> LLMResponse:
        raise NotImplementedError

    def info(self) -> Dict[str, Any]:
        """Return a small dict describing this provider — for the
        Studio settings panel + the /api/llm/status endpoint.

        Should NEVER include credentials — only the public-facing
        config (which model, which endpoint, etc.).
        """
        return {"name": self.name}

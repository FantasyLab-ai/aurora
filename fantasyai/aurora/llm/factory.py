"""
Provider factory + the public ``generate`` function.

Reads ``AURORA_LLM_PROVIDER`` from the environment (or accepts an
override) and instantiates the right backend. Caches the provider so
repeated calls don't re-construct it.

Switching providers is a single env-var change — no code edits.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMResponse, LLMConfigError, LLMCallError

LOG = logging.getLogger(__name__)


# Canonical provider identifiers. Match the env-var values.
PROVIDER_OLLAMA = "ollama"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"
PROVIDER_GENERIC_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_NONE = "none"   # synthesis disabled — math runs but no narrative


# Cached singleton. Reset when env changes via ``get_provider(reload=True)``.
_provider_cache: Dict[str, LLMProvider] = {}


def _resolve_provider_name(override: Optional[str] = None) -> str:
    name = (override
            or os.environ.get("AURORA_LLM_PROVIDER")
            or "").strip().lower()
    if not name:
        return PROVIDER_NONE
    return name


def get_provider(override: Optional[str] = None, *, reload: bool = False) -> LLMProvider:
    """Return (constructing if needed) the provider for the current
    configuration.

    Args:
        override:  Override the env var. Useful for the Studio settings
                   UI that wants to test a different provider live.
        reload:    Bypass the cache and reconstruct.

    Raises:
        LLMConfigError: provider name is unknown or its required
        credentials are missing.
    """
    name = _resolve_provider_name(override)

    if not reload and name in _provider_cache:
        return _provider_cache[name]

    if name == PROVIDER_NONE:
        from .none_provider import NoneProvider
        prov = NoneProvider()
    elif name == PROVIDER_OLLAMA:
        from .ollama import OllamaProvider
        prov = OllamaProvider()
    elif name == PROVIDER_ANTHROPIC:
        from .anthropic import AnthropicProvider
        prov = AnthropicProvider()
    elif name == PROVIDER_OPENAI:
        from .openai_provider import OpenAIProvider
        prov = OpenAIProvider()
    elif name == PROVIDER_GEMINI:
        from .gemini import GeminiProvider
        prov = GeminiProvider()
    elif name == PROVIDER_GENERIC_OPENAI_COMPATIBLE:
        from .generic_openai import GenericOpenAIProvider
        prov = GenericOpenAIProvider()
    else:
        raise LLMConfigError(
            f"unknown LLM provider {name!r}. Supported: "
            f"ollama, anthropic, openai, gemini, openai_compatible, none."
        )

    _provider_cache[name] = prov
    return prov


def get_provider_info() -> Dict[str, Any]:
    """Return a settings-UI-friendly dict describing the current
    provider. NEVER includes credentials."""
    try:
        return get_provider().info()
    except LLMConfigError as e:
        return {"name": _resolve_provider_name(),
                "status": "misconfigured",
                "error": str(e)}


def generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    provider_override: Optional[str] = None,
    **kwargs: Any,
) -> LLMResponse:
    """Top-level public entry. Generate text from whichever provider
    is configured.

    Always returns an ``LLMResponse``. The ``none`` provider returns
    an empty string + ``provider='none'`` so callers can fall back to
    template-based narratives.
    """
    prov = get_provider(provider_override)
    t0 = time.perf_counter()
    try:
        resp = prov.generate(
            prompt=prompt, system=system,
            temperature=temperature, max_tokens=max_tokens,
            **kwargs,
        )
        # Stamp latency if the provider didn't already.
        if resp.latency_s == 0.0:
            object.__setattr__(resp, "latency_s",
                                 round(time.perf_counter() - t0, 4))
        return resp
    except LLMCallError:
        raise
    except Exception as e:
        # Wrap any unexpected failure so callers don't have to catch
        # provider-specific exceptions.
        raise LLMCallError(
            f"LLM provider {prov.name!r} failed: {type(e).__name__}: {e}"
        ) from e

"""
Aurora LLM provider abstraction (Stream 2.4 Phase 1).

Aurora's synthesis layer needs an LLM to paraphrase findings into
natural-language narratives. Until v1.2 this was hard-coded to a local
Ollama endpoint. For Aurora Cloud (and any user who wants Claude /
OpenAI / Gemini instead of running Gemma locally), we need a plug-in
provider model:

  * One unified ``generate(prompt, ...)`` interface
  * Backends for Ollama (default), Anthropic, OpenAI, Gemini, and any
    generic OpenAI-compatible HTTP endpoint
  * Provider chosen by env var or config (``AURORA_LLM_PROVIDER``)
  * Credentials read from env vars per provider (never logged)
  * Synthesis prompts are the same regardless of provider — Aurora's
    contract doesn't change based on who's holding the LLM

This module is the boundary. The synthesis layer calls
``llm.generate(...)`` without caring which backend handles it.

Usage:

    from fantasyai.aurora.llm import generate, get_provider_info

    text = generate(
        prompt="Summarise these findings: ...",
        system="You are Aurora's synthesis layer. Cite only the seed IDs provided.",
        temperature=0.0,
        max_tokens=400,
    )

The first call lazily constructs the provider; subsequent calls reuse
it. Costs (when applicable) are billed directly to the user's chosen
provider — Aurora itself never pays for tokens.
"""
from __future__ import annotations

from .base import (
    LLMResponse,
    LLMProvider,
    LLMConfigError,
    LLMCallError,
)
from .factory import (
    get_provider,
    generate,
    get_provider_info,
    PROVIDER_OLLAMA,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_GENERIC_OPENAI_COMPATIBLE,
    PROVIDER_NONE,
)

__all__ = [
    "LLMResponse",
    "LLMProvider",
    "LLMConfigError",
    "LLMCallError",
    "get_provider",
    "generate",
    "get_provider_info",
    "PROVIDER_OLLAMA",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_OPENAI",
    "PROVIDER_GEMINI",
    "PROVIDER_GENERIC_OPENAI_COMPATIBLE",
    "PROVIDER_NONE",
]

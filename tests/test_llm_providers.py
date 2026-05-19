"""
Tests for the LLM provider abstraction (Stream 2.4 Phase 1).

These are *hermetic* — we never make real HTTP calls. We mock the
``post_json`` helper to return canned responses and verify each
provider:

  * Constructs cleanly given correct env
  * Refuses to construct when required env is missing
  * Parses the provider's response shape correctly into ``LLMResponse``
  * Surfaces provider HTTP errors as ``LLMCallError``
  * Never leaks credentials via ``info()``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.llm import (
    generate,
    get_provider,
    get_provider_info,
    LLMConfigError,
    LLMCallError,
    LLMResponse,
    PROVIDER_NONE,
    PROVIDER_OLLAMA,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDER_GEMINI,
    PROVIDER_GENERIC_OPENAI_COMPATIBLE,
)


# ===========================================================================
# Factory + dispatch
# ===========================================================================

class TestFactory:

    def test_unknown_provider_raises(self):
        with pytest.raises(LLMConfigError, match="unknown LLM provider"):
            get_provider("zzz_not_a_provider", reload=True)

    def test_none_provider_works_without_credentials(self, monkeypatch):
        monkeypatch.delenv("AURORA_LLM_PROVIDER", raising=False)
        prov = get_provider(PROVIDER_NONE, reload=True)
        resp = prov.generate("hello")
        assert resp.text == ""
        assert resp.provider == "none"

    def test_provider_info_never_includes_credentials(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-that-should-not-leak")
        prov = get_provider(PROVIDER_ANTHROPIC, reload=True)
        info = prov.info()
        # No field in info should contain the literal key.
        for k, v in info.items():
            assert "sk-secret" not in str(v), f"credential leaked in {k}"


# ===========================================================================
# Ollama
# ===========================================================================

class TestOllama:

    def test_ollama_parses_response(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "test-model")

        fake_resp = (200, {
            "response": "hello from ollama",
            "eval_count": 7,
            "prompt_eval_count": 4,
            "total_duration": 1234567,
        })
        with patch("fantasyai.aurora.llm.ollama.post_json", return_value=fake_resp):
            prov = get_provider(PROVIDER_OLLAMA, reload=True)
            resp = prov.generate("hi", max_tokens=100)
        assert resp.text == "hello from ollama"
        assert resp.provider == "ollama"
        assert resp.model == "test-model"
        assert resp.usage["eval_count"] == 7

    def test_ollama_surfaces_http_errors(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        fake_resp = (500, {"error": "no model loaded"})
        with patch("fantasyai.aurora.llm.ollama.post_json", return_value=fake_resp):
            prov = get_provider(PROVIDER_OLLAMA, reload=True)
            with pytest.raises(LLMCallError, match="HTTP 500"):
                prov.generate("hi")


# ===========================================================================
# Anthropic
# ===========================================================================

class TestAnthropic:

    def test_anthropic_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
            get_provider(PROVIDER_ANTHROPIC, reload=True)

    def test_anthropic_parses_response(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        fake_resp = (200, {
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "text", "text": "claude says hi"},
                {"type": "text", "text": " — second chunk"},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 8},
        })
        with patch("fantasyai.aurora.llm.anthropic.post_json", return_value=fake_resp):
            prov = get_provider(PROVIDER_ANTHROPIC, reload=True)
            resp = prov.generate("hi", system="you are helpful")
        assert resp.text == "claude says hi — second chunk"
        assert resp.usage["input_tokens"] == 12

    def test_anthropic_surfaces_400(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        fake_resp = (400, {"error": {"message": "invalid request"}})
        with patch("fantasyai.aurora.llm.anthropic.post_json", return_value=fake_resp):
            prov = get_provider(PROVIDER_ANTHROPIC, reload=True)
            with pytest.raises(LLMCallError, match="HTTP 400"):
                prov.generate("hi")


# ===========================================================================
# OpenAI
# ===========================================================================

class TestOpenAI:

    def test_openai_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(LLMConfigError, match="OPENAI_API_KEY"):
            get_provider(PROVIDER_OPENAI, reload=True)

    def test_openai_parses_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        fake_resp = (200, {
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "hi from openai"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3,
                       "total_tokens": 8},
        })
        with patch("fantasyai.aurora.llm.openai_provider.post_json",
                    return_value=fake_resp):
            prov = get_provider(PROVIDER_OPENAI, reload=True)
            resp = prov.generate("hi")
        assert resp.text == "hi from openai"
        assert resp.usage["total_tokens"] == 8


# ===========================================================================
# Gemini
# ===========================================================================

class TestGemini:

    def test_gemini_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
            get_provider(PROVIDER_GEMINI, reload=True)

    def test_gemini_parses_response(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")
        fake_resp = (200, {
            "candidates": [{
                "content": {"parts": [{"text": "hi from gemini"}]},
            }],
            "usageMetadata": {"promptTokenCount": 4,
                              "candidatesTokenCount": 3,
                              "totalTokenCount": 7},
        })
        with patch("fantasyai.aurora.llm.gemini.post_json", return_value=fake_resp):
            prov = get_provider(PROVIDER_GEMINI, reload=True)
            resp = prov.generate("hi")
        assert resp.text == "hi from gemini"
        assert resp.usage["total_token_count"] == 7


# ===========================================================================
# Generic OpenAI-compatible
# ===========================================================================

class TestGenericOpenAI:

    def test_requires_both_env_vars(self, monkeypatch):
        monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
        with pytest.raises(LLMConfigError):
            get_provider(PROVIDER_GENERIC_OPENAI_COMPATIBLE, reload=True)

    def test_parses_openai_compatible_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://api.groq.com/openai")
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "fake")
        fake_resp = (200, {
            "model": "mixtral-8x7b-32768",
            "choices": [{"message": {"content": "hi from groq"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3,
                       "total_tokens": 8},
        })
        with patch("fantasyai.aurora.llm.generic_openai.post_json",
                    return_value=fake_resp):
            prov = get_provider(PROVIDER_GENERIC_OPENAI_COMPATIBLE, reload=True)
            resp = prov.generate("hi")
        assert resp.text == "hi from groq"


# ===========================================================================
# Top-level generate() with provider override
# ===========================================================================

class TestGenerateTopLevel:

    def test_generate_uses_override(self, monkeypatch):
        """``generate(provider_override='none')`` should always work
        even when no env var is set."""
        monkeypatch.delenv("AURORA_LLM_PROVIDER", raising=False)
        resp = generate("hello", provider_override="none")
        assert resp.provider == "none"

    def test_generate_returns_LLMResponse(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        fake_resp = (200, {
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
        with patch("fantasyai.aurora.llm.anthropic.post_json", return_value=fake_resp):
            resp = generate("hi", provider_override="anthropic")
        assert isinstance(resp, LLMResponse)
        assert resp.text == "ok"

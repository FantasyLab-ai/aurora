"""
Atom 3.4: Copilot seed-redirect detection — Layers A (UI), B (copilot
suggested), C (regex safety net). Layer A is purely frontend; B + C are
deterministic helpers exercised here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


from studio_api import (   # noqa: E402
    _regex_seed_pivot, _copilot_seed_suggestion,
)


# ============================================================
# Layer C — regex safety net
# ============================================================

class TestRegexPivot:

    def test_actually_phrase(self):
        s = _regex_seed_pivot("actually I want to look at fatigue patterns")
        assert s is not None
        assert "fatigue" in s["proposed"].lower()
        assert s["confidence"] == 0.4
        assert s["auto_update"] is False

    def test_forget_phrase(self):
        s = _regex_seed_pivot("forget that, focus on the regime shift")
        assert s is not None
        assert "regime" in s["proposed"].lower()

    def test_lets_look_at(self):
        s = _regex_seed_pivot("let's look at the matchup data instead")
        assert s is not None
        assert "matchup" in s["proposed"].lower()

    def test_now_i_want(self):
        s = _regex_seed_pivot("now I want to see early-season trajectories")
        assert s is not None
        assert "trajector" in s["proposed"].lower() or "early" in s["proposed"].lower()

    def test_no_pivot_returns_none(self):
        assert _regex_seed_pivot("what's the most surprising finding?") is None
        assert _regex_seed_pivot("show me the top 3 anomalies") is None
        assert _regex_seed_pivot("") is None

    def test_proposal_too_short_returns_none(self):
        # If the matched pattern leaves no real noun phrase, don't suggest.
        assert _regex_seed_pivot("actually") is None


# ============================================================
# Layer B — copilot-suggested with confidence bump
# ============================================================

class TestCopilotSuggestion:

    def test_low_confidence_when_no_current_seed(self):
        s = _copilot_seed_suggestion(
            user_message="actually I want to look at fatigue",
            current_seed=None, brief="",
        )
        assert s is not None
        # Without a current seed to compare to, confidence stays at the
        # regex floor (0.4).
        assert s["confidence"] == 0.4

    def test_high_confidence_when_topic_pivot_detected(self):
        s = _copilot_seed_suggestion(
            user_message="actually let's look at the matchup edges",
            current_seed="player fatigue accumulating across back-to-backs",
            brief="",
        )
        assert s is not None
        # Topic shifted from fatigue to matchups → confidence bumps.
        assert s["confidence"] >= 0.7
        assert s["trigger"] == "user_pivoted"
        assert "matchup" in s["proposed"].lower()

    def test_confirming_message_does_not_suggest_pivot(self):
        # User confirms current focus — no pivot phrase, no suggestion.
        s = _copilot_seed_suggestion(
            user_message="give me more detail on that fatigue finding",
            current_seed="player fatigue accumulating across back-to-backs",
            brief="",
        )
        assert s is None

    def test_overlap_with_current_seed_keeps_low_confidence(self):
        # User pivot phrase but proposed text overlaps current seed →
        # keep the regex's low confidence.
        s = _copilot_seed_suggestion(
            user_message="actually I want to look at fatigue accumulating",
            current_seed="player fatigue accumulating across back-to-backs",
            brief="",
        )
        # The pivot pattern fires but the proposal heavily overlaps
        # existing seed; we don't bump.
        if s is not None:
            assert s["confidence"] <= 0.5


# ============================================================
# /api/chat carries seed_suggestion
# ============================================================

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fantasyai.aurora import run_registry as _reg
    _reg._clear_for_tests()
    import studio_api
    monkeypatch.setattr(studio_api, "DEFAULT_OUTPUTS", tmp_path / "outputs")
    studio_api.app.config["TESTING"] = True
    with studio_api.app.test_client() as c:
        yield c


class TestChatEndpoint:

    def test_chat_returns_seed_suggestion_field(self, client, monkeypatch):
        # Force the deterministic-fallback path so the test doesn't need
        # Ollama running.
        import studio_api
        monkeypatch.setattr(studio_api, "_ollama_available",
                             lambda *a, **k: False)
        r = client.post("/api/chat", json={
            "message": "actually I want to look at fatigue patterns",
        })
        assert r.status_code == 200
        j = r.get_json()
        assert j["ok"] is True
        # The suggestion field must always be present (None or dict).
        assert "seed_suggestion" in j
        assert j["seed_suggestion"] is not None
        assert "fatigue" in j["seed_suggestion"]["proposed"].lower()

    def test_chat_no_pivot_no_suggestion(self, client, monkeypatch):
        import studio_api
        monkeypatch.setattr(studio_api, "_ollama_available",
                             lambda *a, **k: False)
        r = client.post("/api/chat", json={
            "message": "what's the most critical finding?",
        })
        j = r.get_json()
        assert j["ok"] is True
        assert j["seed_suggestion"] is None

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


@dataclass
class LLMResult:
    used: bool
    provider: str
    model: str
    seconds: float
    error: Optional[str]
    text: Optional[str]
    raw: Optional[Dict[str, Any]]


def generate_narrative(storycard: Dict[str, Any], max_chars: int = 8000) -> LLMResult:
    provider = (os.getenv("AURORA_LLM_PROVIDER") or "").strip().lower()
    if provider != "ollama":
        return LLMResult(False, provider or "none", "", 0.0, "LLM disabled (set AURORA_LLM_PROVIDER=ollama)", None, None)

    url = (os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL") or "deepseek-r1:8b"

    # Compact prompt payload
    payload = json.dumps(storycard, ensure_ascii=False)
    if len(payload) > max_chars:
        payload = payload[:max_chars] + "\n...[truncated]"

    prompt = (
        "You are Aurora. Write an executive summary of the analysis results below.\n"
        "Requirements:\n"
        "- 8-12 bullets max\n"
        "- Call out key signals, strongest relationships, likely target variable, anomalies\n"
        "- Recommend next 5 analyses\n"
        "- Avoid fluff. Be specific.\n\n"
        f"STORYCARD_JSON:\n{payload}\n"
    )

    t0 = time.time()
    try:
        r = requests.post(
            f"{url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        txt = (data.get("response") or "").strip()
        return LLMResult(True, "ollama", model, time.time() - t0, None, txt, data)
    except Exception as e:
        return LLMResult(False, "ollama", model, time.time() - t0, str(e), None, None)
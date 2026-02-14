from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Any, Optional

import requests


@dataclass
class DeepSeekMathCopilot:
    """
    Very small HTTP wrapper around a local DeepSeek/Ollama endpoint.
    This keeps Aurora decoupled from any specific vendor – you can swap out the
    endpoint or model in config without touching the math layer.
    """
    endpoint: str = "http://127.0.0.1:11434/api/chat"
    model: str = "deepseek-r1:8b"
    timeout: int = 40

    def _build_payload(self, prompt: str) -> Dict[str, Any]:
        # Ollama-style chat payload
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are Aurora, a rigorous but concise math and modeling engine. "
                               "Explain results clearly, avoid speculation, and keep answers under 300 words.",
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }

    def complete(self, prompt: str) -> str:
        try:
            resp = requests.post(
                self.endpoint,
                data=json.dumps(self._build_payload(prompt)),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            # Ollama: {"message":{"role":"assistant","content":"..."}}
            if isinstance(data, dict):
                msg = data.get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    return content
            # Fallback if schema is different.
            return json.dumps(data)[:2000]
        except Exception as exc:  # pragma: no cover - network dependent
            return (
                "[Aurora DeepSeek Copilot – fallback]\n"
                "I attempted to call the local DeepSeek endpoint but it was unreachable.\n"
                f"Endpoint: {self.endpoint}, model: {self.model}.\n"
                "Here is the prompt that would have been sent:\n"
                f"{prompt[:1000]}"
            )


def summarize_math_result(task_name: str, payload: Dict[str, Any], copilot: Optional[DeepSeekMathCopilot] = None) -> str:
    """
    Generic helper to turn a math payload into a concise DeepSeek summary.
    Safe: if copilot is None, returns a deterministic text without external calls.
    """
    # Lightly structure the JSON to keep prompts small but informative.
    compact = json.dumps(payload, indent=2)[:2000]
    user_prompt = (
        f"Aurora task: {task_name}.\n"
        f"Here is a JSON summary of the numeric results:\n"
        f"{compact}\n\n"
        "Please:\n"
        "1) Explain in plain language what this result means.\n"
        "2) Highlight any risks, anomalies, or edge cases.\n"
        "3) Suggest 1–3 next analyses Aurora could run.\n"
    )

    if copilot is None:
        # Deterministic, offline-friendly behavior.
        return (
            "[Aurora offline summary]\n"
            "Task: {task}\n"
            "I see structured results with keys: {keys}.\n"
            "In a real deployment, this would be summarized by DeepSeek.\n"
        ).format(task=task_name, keys=list(payload.keys()))

    return copilot.complete(user_prompt)

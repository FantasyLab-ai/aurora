# fantasyai/aurora/paper_interpreter.py

from __future__ import annotations

from typing import List, Dict, Any, Optional

from ..core.llm_client import LLMClient


def extract_equations_from_text(
    text: str,
    llm: Optional[LLMClient] = None,
) -> List[str]:
    """
    Ask the LLM to extract key equations as plain-text expressions
    from a paper or section.
    """
    client = llm or LLMClient()
    prompt = f"""
You are Aurora, an AI math engine.

Extract the key mathematical equations or expressions from the following text.
Return ONLY a JSON array of strings, where each string is an equation
in a Pythonic syntax where possible (e.g. 'x**2 + y**2 - 1 = 0').

Text:
{text}
"""
    raw = client.complete(prompt=prompt)
    # For safety, we will not attempt to json.loads here – user can inspect raw
    # or we can add parsing later. For now, just return the raw string in a list.
    return [raw]


def summarize_paper_section(
    text: str,
    llm: Optional[LLMClient] = None,
) -> str:
    client = llm or LLMClient()
    prompt = f"""
You are Aurora, an AI research assistant.

Summarize the key ideas, assumptions, and results from the following
paper excerpt. Focus especially on:
  - What is being modeled
  - Key mathematical structures (equations, systems, etc.)
  - Any stated open problems or limitations

Text:
{text}
"""
    return client.complete(prompt=prompt)

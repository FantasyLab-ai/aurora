# fantasyai/aurora/retrieval.py

from __future__ import annotations

from typing import List, Tuple

from .embedding_store import store_problem_embedding, find_similar_problems
from .problem_spec import ProblemSpec


def build_problem_text(spec: ProblemSpec) -> str:
    """
    Serialize ProblemSpec into a textual representation suitable
    for simple embedding & similarity search.
    """
    lines = [
        f"problem_type: {spec.problem_type}",
        f"variables: {[v.name for v in spec.variables]}",
        f"meta_domain: {spec.meta.get('domain', 'unknown')}",
    ]
    if spec.equations:
        lines.append("equations:")
        for e in spec.equations:
            lines.append(f"  {e.expression}")
    if spec.optimization is not None:
        lines.append(f"objective: {spec.optimization.objective}")
        if spec.optimization.constraints:
            lines.append("constraints:")
            for c in spec.optimization.constraints:
                lines.append(f"  {c}")
    return "\n".join(lines)


def index_problem_for_retrieval(problem_id: str, spec: ProblemSpec) -> None:
    text = build_problem_text(spec)
    raw = {
        "problem_type": spec.problem_type,
        "meta": spec.meta,
    }
    store_problem_embedding(problem_id, text, raw)


def query_similar_problems(query_text: str, top_k: int = 5):
    return find_similar_problems(query_text, top_k=top_k)

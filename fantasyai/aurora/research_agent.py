# fantasyai/aurora/research_agent.py

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Optional, List

from .problem_spec import ProblemSpec
from .ensemble import (
    solve_equation_ensemble,
    solve_optimization_ensemble,
)
from .experiment_lab import (
    sweep_initial_values,
    random_initial_search,
)
from .research_memory import log_experiment, load_experiments
from ..core.llm_client import LLMClient
from .math_lab import MathSolution


class AuroraResearchAgent:
    """
    High-level orchestrator that:
      - runs ensemble solvers
      - launches sweeps / random searches
      - logs everything to research_memory
      - uses LLM for explanation & next steps
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()

    # -----------------------------
    # Core solve entrypoints
    # -----------------------------

    def solve_equations(self, spec: ProblemSpec, *, tag: Optional[str] = None) -> MathSolution:
        sol = solve_equation_ensemble(spec, llm=self.llm, explain=True, tag=tag)
        return sol

    def solve_optimization(self, spec: ProblemSpec, *, tag: Optional[str] = None) -> MathSolution:
        sol = solve_optimization_ensemble(spec, llm=self.llm, explain=True, tag=tag)
        return sol

    # -----------------------------
    # Experiment-style helpers
    # -----------------------------

    def run_sweep(
        self,
        spec: ProblemSpec,
        var_name: str,
        values: List[float],
        *,
        problem_kind: str = "equation_system",
        tag_prefix: str = "sweep",
    ) -> List[MathSolution]:
        return sweep_initial_values(
            spec,
            var_name,
            values,
            problem_kind=problem_kind,
            llm=self.llm,
            tag_prefix=tag_prefix,
        )

    def run_random_search(
        self,
        spec: ProblemSpec,
        var_bounds: Dict[str, tuple[float, float]],
        num_samples: int = 20,
        *,
        problem_kind: str = "optimization",
        tag_prefix: str = "random_search",
    ) -> List[MathSolution]:
        return random_initial_search(
            spec,
            var_bounds=var_bounds,
            num_samples=num_samples,
            problem_kind=problem_kind,
            llm=self.llm,
            tag_prefix=tag_prefix,
        )

    # -----------------------------
    # Memory exploration
    # -----------------------------

    def summarize_past_experiments(self, max_items: int = 10) -> str:
        """
        Use the LLM to summarize recent Aurora experiments from memory.
        """
        records = load_experiments()
        if not records:
            return "No experiments logged yet."

        recent = records[-max_items:]

        prompt = f"""
You are Aurora, the AI research engine.

Here are some recent experiment records (truncated to {max_items}):

{recent}

Summarize:
  1) What kinds of problems were attempted
  2) Which methods seemed to work best
  3) Any visible patterns or next-step recommendations
Keep it concise but insightful.
"""

        return self.llm.complete(prompt=prompt)

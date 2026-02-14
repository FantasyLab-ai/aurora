# fantasyai/aurora/problem_spec.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Literal


MathProblemType = Literal["equation_system", "optimization", "ode", "custom"]


@dataclass
class VariableSpec:
    """
    Represents a single variable in a math problem.

    Example:
        VariableSpec(name="x", lower=-10, upper=10, initial=1.0)
    """
    name: str
    initial: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None


@dataclass
class EquationSpec:
    """
    Represents a single equation or residual expression.

    For equation_system:
        expression: e.g. "x**2 + y**2 - 1"
    For ODE:
        expression: e.g. "diff(y(x), x) - (x*y(x))"
    """
    expression: str
    weight: float = 1.0
    description: Optional[str] = None


@dataclass
class OptimizationSpec:
    """
    Represents an objective function and optional constraints.

    objective: expression to minimize (e.g. "(x-2)**2 + (y+1)**2")
    constraints: list of inequality/equality expressions (e.g. "x >= 0")
    """
    objective: str
    constraints: List[str] = field(default_factory=list)


@dataclass
class ProblemSpec:
    """
    High-level problem description that Aurora's math_lab will consume.

    You can grow this over time as you add more capabilities.
    """
    problem_type: MathProblemType

    # User readable description (for logs / LLM)
    description: str

    # Variables in the problem
    variables: List[VariableSpec] = field(default_factory=list)

    # For equation systems / ODEs
    equations: List[EquationSpec] = field(default_factory=list)

    # For optimization problems
    optimization: Optional[OptimizationSpec] = None

    # Additional metadata / tags / domain context
    meta: Dict[str, Any] = field(default_factory=dict)

    def variable_names(self) -> List[str]:
        return [v.name for v in self.variables]

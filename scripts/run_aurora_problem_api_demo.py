from __future__ import annotations

from fantasyai.aurora.problem_api import (
    VarSpec,
    ObjectiveSpec,
    ConstraintSpec,
    ProblemSpec,
    solve_problem_with_scipy,
)


def _build_example_problem() -> ProblemSpec:
    """
    Simple production planning problem:

    Maximize profit:
        max 40*x1 + 30*x2

    Subject to:
        2*x1 + 1*x2 <= 100  (capacity constraint 1)
        1*x1 + 2*x2 <= 80   (capacity constraint 2)
        x1, x2 >= 0
    """

    vars_ = [
        VarSpec(name="x1", lower=0.0, upper=None, initial=10.0),
        VarSpec(name="x2", lower=0.0, upper=None, initial=10.0),
    ]

    def profit(vars_dict):
        return 40.0 * vars_dict["x1"] + 30.0 * vars_dict["x2"]

    objective = ObjectiveSpec(
        sense="max",
        func=profit,
        description="Maximize production profit of two product lines.",
    )

    def c1(vars_dict):
        # 2*x1 + x2 <= 100 -> 2*x1 + x2 - 100 <= 0
        return 2.0 * vars_dict["x1"] + 1.0 * vars_dict["x2"] - 100.0

    def c2(vars_dict):
        # x1 + 2*x2 <= 80 -> x1 + 2*x2 - 80 <= 0
        return 1.0 * vars_dict["x1"] + 2.0 * vars_dict["x2"] - 80.0

    constraints = [
        ConstraintSpec(func=c1, sense="<=", description="Capacity constraint 1"),
        ConstraintSpec(func=c2, sense="<=", description="Capacity constraint 2"),
    ]

    meta = {
        "domain": "logistics/production",
        "note": "Toy problem to demonstrate the Aurora problem DSL.",
    }

    return ProblemSpec(
        variables=vars_,
        objective=objective,
        constraints=constraints,
        meta=meta,
    )


def main() -> None:
    print("=" * 80)
    print("[AURORA PROBLEM API] Production planning demo")
    print("=" * 80)

    spec = _build_example_problem()
    res = solve_problem_with_scipy(spec)

    print("\nSolution:")
    print(f"  success          : {res['success']}")
    print(f"  status           : {res['status']}")
    print(f"  message          : {res['message']}")
    print(f"  objective_value  : {res['objective_value']:.4f}")

    print("  variables:")
    for name, val in res["solution"].items():
        print(f"    - {name} = {val:.4f}")

    print("\nMeta:")
    for k, v in res["meta"].items():
        print(f"  {k}: {v}")

    print("\nInterpretation:")
    print("  This mini-DSL is what your frontend can target: define variables,")
    print("  constraints, and objectives in JSON/yaml, then Aurora compiles it")
    print("  into a concrete solver call and returns a structured result.\n")

    print("[AURORA PROBLEM API] Demo completed.")
    print("=" * 80)


if __name__ == "__main__":
    main()

# fantasyai/aurora/strategy_model.py

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from .problem_features import extract_problem_features
from .research_memory import load_experiments


try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import OneHotEncoder
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
except ImportError:  # pragma: no cover
    RandomForestClassifier = None  # type: ignore
    ColumnTransformer = None       # type: ignore
    OneHotEncoder = None           # type: ignore
    Pipeline = None                # type: ignore


@dataclass
class StrategyConfig:
    """
    A small wrapper that describes which solver strategy to use.
    """
    solver_family: str   # e.g. "equation_ensemble", "optimization_ensemble"
    restarts: int = 5
    use_nsolve: bool = True
    notes: str = ""


MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),  # fantasyai/
    "data",
    "aurora_strategy_model.json",
)


class AuroraStrategyModel:
    """
    Tiny meta-model that maps problem features -> strategy choice.

    For simplicity:
      - implemented with scikit-learn RandomForest
      - saved as a JSON structure (we don't persist the exact RF weights here;
        you can extend this to joblib if you want).
    """

    def __init__(self) -> None:
        self._clf = None
        self._feature_columns: List[str] = []
        self._categorical_cols: List[str] = []
        self._pipeline = None

    def is_available(self) -> bool:
        return RandomForestClassifier is not None

    def train_from_memory(self, memory_path: Optional[str] = None) -> None:
        if not self.is_available():
            raise RuntimeError("scikit-learn is not installed; cannot train strategy model.")

        records = load_experiments(path=memory_path) if memory_path else load_experiments()
        if not records:
            raise RuntimeError("No experiments found in research memory; nothing to train on.")

        # Build dataset
        X: List[Dict[str, Any]] = []
        y: List[str] = []

        for rec in records:
            spec = rec.get("spec", {})
            sol = rec.get("solution", {})
            method = sol.get("method", "")
            success = sol.get("success", False)

            if not spec or not method:
                continue

            # We'll treat method names as labels for now
            problem_type = spec.get("problem_type", "unknown")
            meta = spec.get("meta", {})
            domain = meta.get("domain", "unknown")

            # Rehydrate minimal ProblemSpec-like dict for feature extractor
            pseudo_spec = ProblemSpecCompatible.from_dict(spec, meta_domain=domain)
            feats = extract_problem_features(pseudo_spec)
            feats["problem_type"] = problem_type

            X.append(feats)
            label = method if success else f"{method}_fail"
            y.append(label)

        if not X:
            raise RuntimeError("No valid training examples constructed from memory.")

        # Assemble into tabular form
        import pandas as pd  # type: ignore

        df = pd.DataFrame(X)
        target = y

        numeric_cols = df.select_dtypes(include=["int64", "float64", "bool"]).columns.tolist()
        categorical_cols = [c for c in df.columns if c not in numeric_cols]

        self._feature_columns = list(df.columns)
        self._categorical_cols = categorical_cols

        # Build preprocessing + classifier
        preprocessor = ColumnTransformer(
            transformers=[
                ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_cols),
            ],
            remainder="passthrough",
        )

        clf = RandomForestClassifier(
            n_estimators=50,
            random_state=42,
        )

        pipe = Pipeline(
            steps=[
                ("preprocess", preprocessor),
                ("clf", clf),
            ]
        )

        pipe.fit(df, target)
        self._pipeline = pipe

        # Persist minimal metadata
        self._save_metadata()

    def _save_metadata(self) -> None:
        if self._pipeline is None:
            return

        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        meta = {
            "feature_columns": self._feature_columns,
            "categorical_cols": self._categorical_cols,
        }
        with open(MODEL_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f)

    def load_metadata(self) -> None:
        if not os.path.exists(MODEL_PATH):
            return
        with open(MODEL_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        self._feature_columns = meta.get("feature_columns", [])
        self._categorical_cols = meta.get("categorical_cols", [])

    def predict_strategy(self, spec: "ProblemSpecCompatible") -> StrategyConfig:
        """
        Predict a StrategyConfig for a given problem.
        If no model is trained, returns a reasonable default.
        """
        if self._pipeline is None or not self._feature_columns:
            # Fallback heuristic
            if spec.problem_type == "optimization":
                return StrategyConfig(
                    solver_family="optimization_ensemble",
                    restarts=10,
                    use_nsolve=False,
                    notes="fallback: optimization with restarts",
                )
            else:
                return StrategyConfig(
                    solver_family="equation_ensemble",
                    restarts=0,
                    use_nsolve=True,
                    notes="fallback: equation ensemble with analytic+numeric",
                )

        feats = extract_problem_features(spec)
        import pandas as pd  # type: ignore
        df = pd.DataFrame([feats])

        # Align columns
        for col in self._feature_columns:
            if col not in df.columns:
                df[col] = 0
        df = df[self._feature_columns]

        label = self._pipeline.predict(df)[0]

        # Map label to StrategyConfig (simple heuristic)
        if "opt" in label or "minimize" in label:
            return StrategyConfig(
                solver_family="optimization_ensemble",
                restarts=10,
                use_nsolve=False,
                notes=f"predicted from label {label}",
            )

        return StrategyConfig(
            solver_family="equation_ensemble",
            restarts=5,
            use_nsolve=("nsolve" in label),
            notes=f"predicted from label {label}",
        )


class ProblemSpecCompatible:
    """
    Minimal façade so strategy model can work with dicts from memory
    or real ProblemSpec objects.
    """
    def __init__(self, problem_type: str, meta_domain: str, num_vars: int, num_eqs: int, eq_exprs: list[str]):
        from .problem_spec import VariableSpec, EquationSpec, ProblemSpec
        self.problem_type = problem_type
        self.meta = {"domain": meta_domain}
        self.variables = [VariableSpec(name=f"x{i}") for i in range(num_vars)]
        self.equations = [EquationSpec(expression=e) for e in eq_exprs]

    @classmethod
    def from_dict(cls, d: Dict[str, Any], meta_domain: str = "unknown") -> "ProblemSpecCompatible":
        vars_ = d.get("variables", [])
        eqs_ = d.get("equations", [])
        num_vars = len(vars_)
        num_eqs = len(eqs_)
        exprs = [eq.get("expression", "") for eq in eqs_]
        return cls(
            problem_type=d.get("problem_type", "equation_system"),
            meta_domain=meta_domain,
            num_vars=num_vars,
            num_eqs=num_eqs,
            eq_exprs=exprs,
        )

# scripts/train_aurora_strategy.py

from __future__ import annotations

from fantasyai.aurora.strategy_model import AuroraStrategyModel


def main():
    print("[AURORA STRATEGY] Training meta-solver from research memory...")
    model = AuroraStrategyModel()
    if not model.is_available():
        print("[AURORA STRATEGY] scikit-learn not installed; cannot train. Please `pip install scikit-learn`.")
        return

    model.train_from_memory()
    print("[AURORA STRATEGY] Training complete. Metadata saved.")

if __name__ == "__main__":
    main()

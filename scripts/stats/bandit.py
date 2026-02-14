# scripts/stats/bandit.py
from __future__ import annotations
import json, math, time, random
from pathlib import Path
import numpy as np

class BetaBandit:
    def __init__(self, names: list[str], store: Path):
        self.names = names
        self.store = store
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.state = {n: {"alpha": 1.0, "beta": 1.0} for n in names}
        if store.exists():
            self.state.update(json.loads(store.read_text()))

    def select(self) -> str:
        draws = {n: np.random.beta(self.state[n]["alpha"], self.state[n]["beta"]) for n in self.names}
        return max(draws, key=draws.get)

    def update(self, name: str, reward: float|int):
        # reward in [0,1], can be binary (click) or soft (normalized retention)
        a, b = self.state[name]["alpha"], self.state[name]["beta"]
        self.state[name]["alpha"], self.state[name]["beta"] = a + float(reward), b + (1.0 - float(reward))
        self.store.write_text(json.dumps(self.state, indent=2))

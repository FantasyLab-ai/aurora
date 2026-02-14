# scripts/aurora_dashboard.py

from __future__ import annotations

from collections import Counter
from typing import Dict, Any, List

from fantasyai.aurora.research_memory import load_experiments
from fantasyai.aurora.research_agent import AuroraResearchAgent


def summarize_experiments(records: List[Dict[str, Any]]) -> None:
    if not records:
        print("No experiments found.")
        return

    methods = Counter()
    success_by_method = Counter()
    domains = Counter()

    for rec in records:
        spec = rec.get("spec", {})
        sol = rec.get("solution", {})

        method = sol.get("method", "unknown")
        success = sol.get("success", False)
        meta = spec.get("meta", {})
        domain = meta.get("domain", "unknown")

        methods[method] += 1
        domains[domain] += 1
        if success:
            success_by_method[method] += 1

    print("\n=== Aurora Experiment Summary ===")
    print(f"Total experiments: {len(records)}")

    print("\nMethods used:")
    for m, cnt in methods.most_common():
        succ = success_by_method[m]
        rate = (succ / cnt * 100.0) if cnt else 0.0
        print(f"  {m}: {cnt} runs (success rate {rate:.1f}%)")

    print("\nDomains seen:")
    for d, cnt in domains.most_common():
        print(f"  {d}: {cnt} experiments")


def main():
    records = load_experiments()
    summarize_experiments(records)

    agent = AuroraResearchAgent()
    print("\n=== Aurora LLM Summary of Recent Experiments ===")
    print(agent.summarize_past_experiments(max_items=10))


if __name__ == "__main__":
    main()

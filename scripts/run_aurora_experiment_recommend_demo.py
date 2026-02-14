# scripts/run_aurora_experiment_recommend_demo.py

"""
Aurora Experiment Recommendation Demo

Shows how to:
  - summarize Aurora's experiment log
  - query for similar experiments based on numeric metrics
"""

from fantasyai.aurora.research_memory import (
    summarize_experiment_history,
    find_similar_experiments,
)


def main() -> None:
    print("=" * 80)
    print("[AURORA Experiment Memory Demo]")
    print("=" * 80)

    summary = summarize_experiment_history()
    print("\n[Summary] Experiment log:")
    print(f"  Num records: {summary['num_records']}")
    print(f"  Tag counts: {summary['tag_counts']}")
    print("  Recent examples (truncated):")
    for rec in summary["recent_examples"]:
        print(f"    - tag={rec.get('tag')} timestamp={rec.get('timestamp')}")

    # Example: use generic numeric keys we know appear in some solutions
    numeric_keys = ["ate_ipw", "concordance_index", "rmse", "r2"]

    # Query record: pretend we just ran a new AML experiment with certain metrics
    query = {
        "solution": {
            "ate_ipw": 0.5,
            "concordance_index": 0.7,
            "rmse": 10.0,
            "r2": 0.6,
        },
        "tag": "demo_query",
    }

    sim_res = find_similar_experiments(query, numeric_keys=numeric_keys, top_k=5)

    print("\n[Similar Experiments]")
    print(f"  Num considered: {sim_res['num_considered']}")
    for m in sim_res["matches"]:
        rec = m["record"]
        print(
            f"  - sim={m['similarity']:.3f}, "
            f"tag={rec.get('tag')}, "
            f"timestamp={rec.get('timestamp')}"
        )

    print("\n[AURORA] Experiment memory demo complete.")


if __name__ == "__main__":
    main()

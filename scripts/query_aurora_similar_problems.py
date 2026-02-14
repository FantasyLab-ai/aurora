# scripts/query_aurora_similar_problems.py

from __future__ import annotations

from fantasyai.aurora.retrieval import query_similar_problems


def main():
    query_text = input("Enter a problem description to search similar past problems:\n> ")

    results = query_similar_problems(query_text, top_k=5)

    print("\n[AURORA RETRIEVAL] Top similar problems:")
    for ep, score in results:
        print(f"- ID: {ep.id}  score={score:.3f}")
        print(f"  Text snippet: {ep.text[:200].replace('\\n', ' ')}")
        print()

if __name__ == "__main__":
    main()

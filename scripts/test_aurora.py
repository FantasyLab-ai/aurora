import json
import pprint

from fantasyai.schedulers.aurora_scheduler import build_aurora_pipeline
from fantasyai.aurora.storycards import build_latest_motif_storycard_with_world
from fantasyai.aurora.relationships_pipeline import build_relationship_graph_from_raw


def main():
    print("[AURORA] Building pipeline and running it...")
    pipe = build_aurora_pipeline()
    ctx = pipe.run()

    # Basic sanity checks
    print("\n[AURORA] Context keys:")
    print(sorted(ctx.keys()))

    raw_datasets = ctx.get("raw_datasets", [])
    if not raw_datasets:
        print("\n[ERROR] Aurora: no raw_datasets returned. Check data collectors.")
        return

    print(f"\n[AURORA] Got {len(raw_datasets)} raw datasets")

    # Build storycard + world constraints directly
    print("\n[AURORA] Building latest motif storycard + world constraints...")
    bundle = build_latest_motif_storycard_with_world(
        raw_datasets=raw_datasets,
        window=7,
        n_motifs=5,
    )

    if "error" in bundle:
        print("[ERROR] Storycard bundle returned error:", bundle["error"])
        return

    motif_id = bundle["motif_id"]
    storycard = bundle["storycard"]
    world_constraints = bundle["world_constraints"]

    print(f"\n[AURORA] Latest motif ID: {motif_id}")
    print("\n[AURORA] Storycard summary:")
    print("  Title:", storycard.get("title"))
    print("  Summary:", storycard.get("summary"))
    print("  Domains involved:", storycard.get("domains_involved"))

    print("\n[AURORA] World constraints preview:")
    pprint.pprint({k: world_constraints.get(k) for k in list(world_constraints.keys())[:10]})

    # Relationships
    print("\n[AURORA] Building relationship graph summary...")
    rel_result = build_relationship_graph_from_raw(
        raw_datasets=raw_datasets,
        max_lag=5,
        min_strength="weak",
    )

    if "error" in rel_result:
        print("[ERROR] Relationship graph error:", rel_result["error"])
    else:
        summary = rel_result["summary"]
        print("\n[AURORA] Relationship summary:")
        print("  Num relationships:", summary["num_relationships"])
        print("  Top domain-domain links:")
        for item in summary["domain_pairs_counts"][:5]:
            print(f"   - {item['source_domain']} → {item['target_domain']}: {item['count']} links")

    print("\n[AURORA] Test completed successfully.")


if __name__ == "__main__":
    main()

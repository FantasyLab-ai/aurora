#!/usr/bin/env python
"""
Aurora KB Pack Builder — for the maintainer (i.e., you).

Builds a single ``{id}-v{n}.tar.gz`` pack from a directory of source
JSONL entries + a sentence-transformer embedder. Emits a manifest-entry
snippet you can paste into ``fantasyai/aurora/knowledge_bank/packs/manifest.json``.

Usage:

    python tools/build_kb_pack.py \\
        --id finance \\
        --name "Financial markets & quantitative finance" \\
        --description "Cited entries covering volatility regimes, ..." \\
        --version 1 \\
        --source path/to/finance_entries.jsonl \\
        --covers-domains finance economics \\
        --covers-methods granger hmm_baum_welch \\
        --output-dir dist/packs/

After the pack is built:

    1. Upload ``dist/packs/finance-v1.tar.gz`` to Cloudflare R2 and to
       the HuggingFace mirror dataset.
    2. Run ``--print-manifest-entry`` to get the JSON snippet (it
       includes the actual sha256 + size).
    3. Paste the snippet into manifest.json, commit, push.
    4. Aurora installs pick it up on next restart / next analysis.

Entry format (entries.jsonl): one JSON object per line, matching the
KnowledgeEntry schema from ``fantasyai/aurora/knowledge_bank/schema.py``.
A minimal entry:

    {"id": "seed:granger_1969",
     "domain": ["finance", "research"],
     "concept_type": "method",
     "name": "Granger causality",
     "definition": "Test for whether one time series ...",
     "references": [{"citation": "Granger, C.W.J. (1969). ...",
                     "doi": "10.2307/1912791"}],
     "pattern_signatures": [{"finding_method": "granger", ...}]}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import List, Optional


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_entries_jsonl(path: Path) -> int:
    """Quick sanity check: every line must be valid JSON with a non-empty 'id'.
    Returns the count of valid entries. Raises ValueError on malformed input."""
    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"entries.jsonl line {line_no} is not valid JSON: {e}")
            if not isinstance(obj, dict):
                raise ValueError(f"entries.jsonl line {line_no} is not a JSON object")
            if not obj.get("id"):
                raise ValueError(f"entries.jsonl line {line_no} missing 'id'")
            count += 1
    return count


def _embed_entries(
    entries_path: Path,
    out_npy: Path,
    embedder_name: str,
) -> int:
    """Compute embeddings for every entry's text. Writes a (N, D) float32
    numpy array to ``out_npy`` aligned with the order of entries.jsonl.

    Returns the embedding dimension.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError as e:
        raise SystemExit(
            f"Pack builder needs sentence-transformers + numpy installed. "
            f"Run: pip install sentence-transformers numpy. ({e})"
        )

    model = SentenceTransformer(embedder_name)
    texts: List[str] = []
    with open(entries_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            # Embed name + definition + any caveats; falls back to the raw
            # JSON if nothing meaningful is present.
            parts = [
                str(obj.get("name", "")),
                str(obj.get("definition", "")),
                " ".join(c.get("text", "") for c in (obj.get("caveats") or [])),
            ]
            text = " — ".join(p for p in parts if p).strip() or json.dumps(obj)[:512]
            texts.append(text)
    if not texts:
        raise ValueError("no entries to embed")
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    arr = np.asarray(vecs, dtype="float32")
    np.save(out_npy, arr)
    return int(arr.shape[1])


def build_pack(args: argparse.Namespace) -> Path:
    src = Path(args.source).resolve()
    if not src.exists():
        raise SystemExit(f"--source {src} does not exist")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{args.id}-v{args.version}.tar.gz"

    # Stage the pack contents in a tempdir.
    with tempfile.TemporaryDirectory(prefix=f"pack_{args.id}_") as tmp:
        tmp_dir = Path(tmp)

        # 1) Validate + copy entries.jsonl.
        entry_count = _validate_entries_jsonl(src)
        if entry_count == 0:
            raise SystemExit(f"--source {src} contains no valid entries")
        entries_dest = tmp_dir / "entries.jsonl"
        entries_dest.write_bytes(src.read_bytes())

        # 2) Compute embeddings → embeddings.npy.
        print(f"[builder] embedding {entry_count} entries with "
              f"{args.embedder!r}...", file=sys.stderr)
        embedding_dim = _embed_entries(entries_dest, tmp_dir / "embeddings.npy",
                                        args.embedder)
        print(f"[builder] embedding dim = {embedding_dim}", file=sys.stderr)

        # 3) Write pack_meta.json.
        meta = {
            "id": args.id,
            "name": args.name,
            "description": args.description,
            "version": args.version,
            "entry_count": entry_count,
            "embedder": args.embedder,
            "embedding_dim": embedding_dim,
            "schema_version": "1.0.0",
        }
        with open(tmp_dir / "pack_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        # 4) Tar + gzip into the archive.
        print(f"[builder] writing {archive}", file=sys.stderr)
        with tarfile.open(archive, "w:gz") as tf:
            for name in ("pack_meta.json", "entries.jsonl", "embeddings.npy"):
                tf.add(tmp_dir / name, arcname=name)

    size_bytes = archive.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    sha = _sha256(archive)

    # Emit a manifest-entry snippet ready to paste.
    snippet = {
        "id": args.id,
        "name": args.name,
        "description": args.description,
        "version": args.version,
        "size_mb": round(size_mb, 2),
        "entry_count": entry_count,
        "embedder": args.embedder,
        "embedding_dim": embedding_dim,
        "covers_methods": list(args.covers_methods or []),
        "covers_domains": list(args.covers_domains or []),
        "url_primary": f"https://kb.aurora.fantasylab.ai/packs/{archive.name}",
        "url_mirror": f"https://huggingface.co/datasets/fantasylab-ai/aurora-kb/resolve/main/{archive.name}",
        "sha256": sha,
        "license": args.license,
        "maintainer": args.maintainer,
        "homepage": args.homepage,
    }
    print(f"\n[builder] DONE", file=sys.stderr)
    print(f"  archive : {archive}", file=sys.stderr)
    print(f"  size    : {size_mb:.2f} MB ({size_bytes} bytes)", file=sys.stderr)
    print(f"  sha256  : {sha}", file=sys.stderr)
    print(f"  entries : {entry_count}", file=sys.stderr)
    print(f"\n[builder] paste this snippet into manifest.json:\n", file=sys.stderr)
    print(json.dumps(snippet, indent=2))
    return archive


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="build_kb_pack",
        description="Build a single Aurora KB pack tarball.",
    )
    p.add_argument("--id", required=True, help="Pack id (snake_case, e.g. 'finance')")
    p.add_argument("--name", required=True, help="Human-readable pack name")
    p.add_argument("--description", required=True, help="One-paragraph summary")
    p.add_argument("--version", type=int, required=True, help="Pack version (integer)")
    p.add_argument("--source", required=True, help="Path to entries.jsonl source file")
    p.add_argument("--output-dir", default="dist/packs", help="Where to write the .tar.gz")
    p.add_argument("--embedder", default="all-MiniLM-L6-v2",
                    help="sentence-transformers model name")
    p.add_argument("--covers-methods", nargs="*", default=[],
                    help="Aurora method names this pack tends to ground")
    p.add_argument("--covers-domains", nargs="*", default=[],
                    help="Domain hints, e.g. 'finance' 'industrial'")
    p.add_argument("--license", default="CC-BY-4.0",
                    help="License of the collection")
    p.add_argument("--maintainer", default="", help="Maintainer name (optional)")
    p.add_argument("--homepage", default="",
                    help="Pack homepage URL (optional)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    build_pack(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())

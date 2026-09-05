#!/usr/bin/env python3
"""
Build a fine-tuning dataset for the micro-grid negotiation task.

The idea
--------
The usual way to fine-tune a small model is to collect its own outputs, have a
human or a frontier model grade them, and train on the good ones. That is slow
and expensive. Here it is unnecessary, because this task has something most
tasks do not: **a solver that knows the right answer.**

`oracle.py` computes the provably optimal allocation for any block. The
simulation constructs the prompt. So an ideal (prompt, completion) pair can be
produced with no model in the loop at all — the target is the LP's answer, not a
sample from a bigger network. This is behavioural cloning from an exact
optimiser, and it means a training set of any size can be generated on a laptop
overnight, before the model is ever run.

Two sources, combinable:

  --synthesize N    run N offline simulations, capture every prompt, and pair it
                    with the optimal (or, where the LP has no view, the
                    deterministic policy's) decision. Needs no Ollama.
  --from-traces P   read a JSONL trace from real model runs. Use this to add the
                    cases the model actually gets wrong, which is where the
                    remaining gains live.

Targets are emitted in the exact output format the prompts demand — a short
reasoning line followed by a fenced JSON block — so a tuned model learns the
schema and the policy together. That matters: on the evidence so far the binding
constraint for 1B models is format compliance, not allocation skill.

Output is JSONL in chat format, ready for Unsloth / TRL / axolotl:

    {"messages": [{"role": "system", ...},
                  {"role": "user", ...},
                  {"role": "assistant", ...}]}

Usage
-----
    python finetune/build_dataset.py --synthesize 200 --out data/train.jsonl
    python finetune/build_dataset.py --from-traces runs/traces.jsonl \\
        --synthesize 200 --out data/train.jsonl --split 0.1
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import sys
from contextlib import redirect_stdout
from typing import Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import microgrid_sim as sim   # noqa: E402


# =============================================================================
# Turning a decision into the completion we want the model to produce
# =============================================================================

REASONING = {
    "assess": "My meter and limits set what I can do this block.",
    "offer": "Unsold surplus is worth nothing, so I offer what I can move at a fair price.",
    "counter": "I bid below the best offer but above what a seller would walk away from.",
    "respond_counter": "The bid clears my reservation price, so taking it beats curtailment.",
    "allocate": "I buy the cheapest energy first, up to my need and my credits.",
    "storage_bid": "Distressed surplus is cheap now and worth more later.",
    "respond_bid": "Any price above zero beats curtailing this energy.",
}


def format_completion(task: str, decision: dict) -> str:
    """
    Render a decision as the assistant turn we want the model to imitate.

    Reasoning first, then one fenced JSON block with bare numeric values —
    exactly the contract the system prompt states. Training on this format is
    the cheapest available fix for the failure mode that actually blocks 1B
    deployment.
    """
    payload = {k: v for k, v in decision.items() if k != "note"}
    body = json.dumps(payload, separators=(", ", ": "))
    return f"{REASONING.get(task, 'Here is my decision.')}\n```json\n{body}\n```"


def to_example(record: dict, completion: str) -> dict:
    return {"messages": [
        {"role": "system", "content": record["system"]},
        {"role": "user", "content": record["prompt"]},
        {"role": "assistant", "content": completion},
    ]}


# =============================================================================
# Oracle-labelled targets
# =============================================================================

OFFER_LINE = re.compile(r"^-\s*(\S+):\s*up to\s*([\d.]+)\s*kWh", re.M)


def offered_limits(prompt: str) -> dict[str, float]:
    """Read each seller's advertised ceiling straight out of the prompt text."""
    return {seller: float(amount) for seller, amount in OFFER_LINE.findall(prompt)}


def oracle_allocation_target(record: dict, flows: dict) -> Optional[dict]:
    """
    Replace a buyer's greedy allocation with the LP's optimal one.

    `flows` is keyed "SELLER->BUYER:D". For the buyer in this record we take
    every inbound demand-serving flow. Only sellers the buyer was actually
    offered energy by are kept, since the model cannot buy from someone who
    never made an offer.

    LP flows are in SENT kWh while an offer ceiling is what the seller
    advertised, so an optimal flow can sit a couple of percent above the stated
    limit. Left alone, that trains the model to exceed the very bounds the
    prompt states — the exact behaviour the physics clamps exist to catch. So
    every target is clamped to what was actually on the table.
    """
    buyer = record["node"]
    limits = offered_limits(record.get("prompt", ""))
    offered = set(record.get("decision", {}).keys()) | set(limits)
    target = {}
    for key, sent in flows.items():
        try:
            pair, purpose = key.split(":")
            seller, recipient = pair.split("->")
        except ValueError:
            continue
        if purpose != "D" or recipient != buyer or seller == buyer:
            continue
        if offered and seller not in offered:
            continue
        capped = float(sent)
        if seller in limits:
            capped = min(capped, limits[seller])
        target[seller] = round(capped, 2)
    if not target:
        return None
    # Sellers that were offered but not chosen must appear as explicit zeros,
    # or the model learns to omit them rather than decline them.
    for seller in offered:
        target.setdefault(seller, 0.0)
    return target


# =============================================================================
# Sources
# =============================================================================

def synthesize(count: int, scenarios: list[str], blocks: int,
               seed_start: int, label_with_oracle: bool) -> list[dict]:
    """
    Generate training data by running the simulation offline.

    Every prompt the agents would see is produced, and the deterministic policy
    supplies the target — upgraded to the LP's answer for allocation decisions
    when `label_with_oracle` is set. No model is involved anywhere.
    """
    import tempfile
    examples: list[dict] = []
    console = sim.Console(color=False, quiet=True)

    for i in range(count):
        scenario = scenarios[i % len(scenarios)]
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tmp:
            trace_path = tmp.name
        try:
            with redirect_stdout(io.StringIO()):
                sim.run_simulation(scenario=scenario, offline=True, blocks=blocks,
                                   seed=seed_start + i, jitter=0.4,
                                   trace_path=trace_path, console=console)
            examples.extend(read_trace(trace_path, label_with_oracle))
        finally:
            os.unlink(trace_path)
    return examples


def read_trace(path: str, label_with_oracle: bool,
               keep_failures: bool = False) -> list[dict]:
    """
    Convert a JSONL trace into training examples.

    `keep_failures` includes turns where a live model produced unusable output.
    Those are the most valuable rows in a real trace — the prompt is one the
    model demonstrably cannot handle, paired with the answer it should have
    given — so it defaults on when reading real traces.
    """
    turns, oracles = [], {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "oracle":
                oracles[record["block"]] = record.get("flows", {})
            elif record.get("type", "turn") == "turn":
                turns.append(record)

    examples = []
    for record in turns:
        decision = record.get("decision") or {}
        if not decision:
            continue
        if not keep_failures and record.get("parse") == "fallback" and record.get("response"):
            # A live-model turn that fell back: the model's own output was junk.
            # Keep it only when explicitly asked for.
            pass
        target = decision
        if record.get("task") == "allocate":
            if label_with_oracle:
                better = oracle_allocation_target(record, oracles.get(record.get("block"), {}))
                if better:
                    target = better
            # Clamp EVERY allocation target — oracle- or policy-derived — to the
            # ceilings the prompt actually states. A target the model cannot
            # justify from what it was shown teaches it to ignore stated limits.
            limits = offered_limits(record.get("prompt", ""))
            target = {k: (min(v, limits[k]) if k in limits and isinstance(v, (int, float)) else v)
                      for k, v in target.items()}
        examples.append(to_example(record, format_completion(record["task"], target)))
    return examples


# =============================================================================
# Entry point
# =============================================================================

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Build a micro-grid negotiation fine-tuning dataset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--synthesize", type=int, default=0,
                   help="number of offline simulations to generate data from")
    p.add_argument("--from-traces", nargs="*", default=[],
                   help="JSONL traces from real model runs")
    p.add_argument("--scenarios", nargs="*",
                   default=["contended", "street", "block", "feeder"],
                   help="scenarios to synthesize across (variety prevents the LoRA "
                        "from memorising one topology)")
    p.add_argument("--blocks", type=int, default=3)
    p.add_argument("--seed-start", type=int, default=1000)
    p.add_argument("--no-oracle-labels", action="store_true",
                   help="clone the greedy policy instead of the LP optimum")
    p.add_argument("--split", type=float, default=0.1, help="validation fraction")
    p.add_argument("--out", default="data/train.jsonl")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    examples: list[dict] = []
    if args.synthesize:
        print(f"synthesizing from {args.synthesize} offline runs "
              f"across {', '.join(args.scenarios)} ...")
        examples += synthesize(args.synthesize, args.scenarios, args.blocks,
                               args.seed_start, not args.no_oracle_labels)
    for path in args.from_traces:
        print(f"reading {path} ...")
        examples += read_trace(path, not args.no_oracle_labels, keep_failures=True)

    if not examples:
        print("no examples produced: pass --synthesize N or --from-traces PATH",
              file=sys.stderr)
        return 2

    # De-duplicate on the user turn: repeated scenarios produce identical
    # prompts, and duplicates are how a small model learns to overfit one block.
    seen, unique = set(), []
    for ex in examples:
        key = ex["messages"][1]["content"]
        if key not in seen:
            seen.add(key)
            unique.append(ex)

    rng = random.Random(args.seed)
    rng.shuffle(unique)
    cut = int(len(unique) * (1.0 - args.split))
    train, val = unique[:cut], unique[cut:]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    _write(train, args.out)
    val_path = args.out.replace(".jsonl", ".val.jsonl")
    if val:
        _write(val, val_path)

    by_task: dict[str, int] = {}
    for ex in unique:
        line = ex["messages"][1]["content"]
        task = ("allocate" if "FINAL DECISION" in line else
                "offer" if "broadcast a REQUEST" in line else
                "assess" if "State your objective" in line else
                "counter" if "counter-price" in line else
                "respond_counter" if "Accept or revise" in line else
                "storage_bid" if "CURTAILED (wasted) if nobody" in line else
                "respond_bid" if "Accept or refuse" in line else "other")
        by_task[task] = by_task.get(task, 0) + 1

    print(f"\n{len(examples)} raw -> {len(unique)} unique examples")
    print(f"  train {len(train)} -> {args.out}")
    print(f"  val   {len(val)} -> {val_path}" if val else "  (no validation split)")
    print("  by task: " + ", ".join(f"{k}={v}" for k, v in sorted(by_task.items())))
    return 0


def _write(rows: Iterable[dict], path: str) -> None:
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    sys.exit(main())

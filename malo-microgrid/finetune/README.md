# Phase 4 — fine-tuning a 1B model into a negotiator

The unusual thing about this task is that **the right answer is computable**.
`oracle.py` solves each block exactly, so training data needs no frontier model
and no human labelling — the target is the LP's answer.

```bash
# 1. generate data (no GPU, no Ollama, minutes)
python finetune/build_dataset.py --synthesize 400 --out data/train.jsonl

# 2. train a LoRA (GPU; ~6 GB VRAM at 3B with QLoRA)
python finetune/train_lora.py --data data/train.jsonl --out adapters/malo-1b

# 3. serve and benchmark it as another arm
ollama create malo-1b -f adapters/malo-1b/Modelfile
python benchmark.py --models llama3.2:1b malo-1b \
    --scenario contended --jitter 0.4 --seeds 30
```

12 offline runs yield roughly 1,900 unique examples, so 400 runs gives a dataset
in the tens of thousands — generated overnight on a laptop.

## What to expect

Prediction, recorded before the experiment so it can be wrong: **structured
compliance rises sharply, allocation efficiency barely moves.** Format is what
1B models fail at; the economics here are simple enough that the deterministic
policy already scores ~92% of optimum.

If that prediction holds, the honest product is a small model that speaks the
protocol reliably in front of a classical solver — still valuable, still
edge-deployable, but not "the LLM does the optimisation". If efficiency *does*
move, that is the stronger result and worth the paper.

Either way, report both numbers. A compliance-only win presented as an
allocation win is the easiest way to make this work unciteable.

## Files

| | |
|---|---|
| `build_dataset.py` | generates (prompt → optimal decision) pairs; tested |
| `train_lora.py` | Unsloth LoRA/QLoRA training; **written but never executed** |

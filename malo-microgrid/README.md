# MALO — Multi-Agent Local-LLM Energy Micro-grid

A research testbed for one question:

> Can ultra-compressed local language models (1B–3B parameters, on cheap edge
> hardware) solve a real-time, physically constrained resource-allocation
> problem through **decentralized negotiation alone** — no central optimizer, no
> solver, no cloud?

Houses on a mesh negotiate electricity in natural language. A physics engine
settles the kWh their conversation agreed on, subject to line capacity,
transmission loss, battery limits and credit budgets. An exact linear program
says what the best possible allocation was, so every result is a number against
a bound rather than an anecdote.

Everything runs offline against [Ollama](https://ollama.com). No API keys, no
egress, no central orchestrator.

---

## Quick start

```bash
pip install -r requirements.txt
python test_microgrid.py                    # 32 tests, no pytest needed

# deterministic control arm — no model, ~1 second
python microgrid_sim.py --offline --scenario contended

# watch a negotiation with a real local model
ollama serve && ollama pull llama3.2:1b
python microgrid_sim.py --scenario contended --trace runs/traces.jsonl

# the sweep that produces an actual result
python benchmark.py --models llama3.2:1b --scenario contended --jitter 0.4 --seeds 30
```

| File | |
|------|---|
| `microgrid_sim.py` | agents, negotiation protocol, physics, parser firewall |
| `oracle.py` | LP optimal allocation — the denominator for every result |
| `scenarios.py` | topologies and role mixes, 3 to 32+ nodes |
| `profiles.py` | solar/demand/EV curves **with provenance tracking** |
| `benchmark.py` | paired multi-seed sweep with confidence intervals |
| `finetune/` | dataset builder (tested) + LoRA training (untested) |

---

## What we have measured so far

All from the deterministic control arm — **no language model has run this yet**.
These are baselines the LLM arms will be measured against.

**1. The brief's 3-node scenario cannot measure anything.** It has one buyer,
and with a single buyer cheapest-first greedy is provably optimal. The control
arm scores **98.7%** of optimum, exactly 100% on 2 of 3 blocks. No method can
demonstrate an advantage in that window.

**2. The optimality gap lives at intermediate connectivity.** Mean allocation
efficiency of greedy dispatch, 12 instances each:

| scenario | topology | mean degree | efficiency | gap |
|---|---|---|---|---|
| block | mesh | 11.0 | 97.5% | 2.5 |
| district | geometric | 6.2 | 94.5% | **5.5** |
| street | geometric | 3.2 | 94.4% | **5.6** |
| street | ring | 2.0 | 94.4% | **5.6** |
| street | star | 1.8 | 95.0% | 5.0 |
| feeder | line | 1.9 | 96.9% | 3.1 |

In a full mesh everyone can reach everyone, so ordering barely matters. In a
line there is only one choice to make. **The room for intelligent negotiation is
in between** — which is also where real distribution networks sit.

**3. Withholding supply is devastating; lying about price is not.** Street
topology, 12 instances:

| behaviour | share of nodes | efficiency | vs honest |
|---|---|---|---|
| honest | — | 94.4% | — |
| misreport (inflates capacity, holds out for ceiling) | 50% | 94.0% | −0.5 |
| dropout (goes silent at random) | 25% | 86.0% | −8.4 |
| dropout | 50% | 80.3% | −14.1 |
| freerider (buys, never sells) | 25% | 76.9% | −17.6 |
| freerider | 50% | 59.3% | **−35.2** |

Price manipulation is self-limiting because settlement enforces physics — a node
that advertises energy it does not have simply fails to deliver it. Supply
withdrawal has no such check. If this holds up, the mechanism design problem is
**incentivising participation, not policing honesty**, which is the opposite of
where most P2P energy market papers put their effort.

---

## Measuring against the optimum

`allocation_efficiency = locally-served demand ÷ the most any method could have
served`, the denominator from an exact LP in `oracle.py` solved on the same
state the agents were handed.

The LP is lexicographic, in physical priority order: **serve demand** (a fossil
peaker not started), then **waste no clean energy** (curtailment destroys it),
then **waste no line losses**. It is restricted to the agents' own action space
— single-hop trades between wired neighbours — so it is a tight bound.

A test asserts the simulation can never beat it, across every scenario and
topology. Building that test found two real physics bugs: a battery rate limit
checked per-trade instead of per-block (so a storage node could discharge at
several times its rated power by selling to several neighbours), and a
same-block relay where a pack charged and re-exported the same energy. Both let
the simulation "beat" the optimum, which is how they were caught.

---

## The hallucination firewall

A 1B model *will* return `"price": "about twelve cents"` and *will* try to sell
40 kWh it does not have. Five layers, each degradation counted:

1. **Fenced-block extraction** — ` ```json `, then any balanced `{…}`, taking
   the *last* parseable object (small models restate corrections at the end).
2. **JSON repair** — trailing commas, bare keys, single quotes, `//` comments,
   Python literals.
3. **Numeric coercion** — `"5.0 kWh"`, `"$0.12"`, `"four"`, `"about 3–4 kWh"`.
4. **Regex salvage** — hunts `key … number` in prose when there is no JSON.
5. **Deterministic fallback** — a real policy: greedy merit-order for buyers,
   scarcity-priced offers for sellers. `--offline` runs it alone as the control.

Every model number then passes physics clamps before it can become a contract.

---

## Data provenance

`profiles.py` refuses to let synthetic data be mistaken for measured data. Every
profile carries a `Provenance` record, one synthetic input marks the whole run
`ILLUSTRATIVE ONLY`, and that verdict prints above every result.

* **measured** — a CSV you supply (PVWatts export, inverter log, Pecan Street).
* **api** — fetched live from NREL PVWatts (free key, one minute to get).
* **synthetic** — a clear-sky model with exact solar geometry (declination,
  hour angle, Kasten-Young air mass) but invented weather. The default, and
  **not citable**.

Getting a PVWatts key is the single cheapest upgrade to the credibility of
anything here.

---

## Edge feasibility

The pitch is a $35 computer in a breaker panel, so every run reports calls,
tokens and wall-clock per trading block, and whether that fits inside a 5-minute
real-time market, a 15-minute settlement interval, or an hourly block. Energy
per block is an **estimate** from an assumed device wattage — measure your own
hardware before publishing it.

Call volume: 23 per 3-block run on `brief`, 66 on `contended`. Budget
accordingly for sweeps.

---

## Prompt ablation

Every prompt technique here is a hypothesis. `--prompt-style` makes each
switchable so a sweep can measure it instead of asserting it:

| style | what it removes |
|---|---|
| `full` | nothing — pre-computed bounds, reasoning-before-JSON, worked example |
| `no-bounds` | pre-computed arithmetic; the model derives its own limits |
| `no-reason` | the reasoning sentence; JSON only |
| `terse` | everything — the naive prompt most people write first |

Expectation, recorded in advance: `no-bounds` hurts 1B far more than 3B, because
the failure is arithmetic rather than instruction-following. If that holds it is
a concrete design rule for edge LLM systems. If not, the prompt engineering here
is cargo cult and should be deleted.

---

## Fine-tuning (Phase 4)

The unusual thing about this task: **the right answer is computable.** The LP
solves each block exactly, so training data needs no frontier model and no human
labelling — the target is the solver's answer. 12 offline runs produce ~1,900
unique examples in seconds; 400 runs give a dataset in the tens of thousands,
generated overnight on a laptop with no GPU and no Ollama.

```bash
python finetune/build_dataset.py --synthesize 400 --out data/train.jsonl
python finetune/train_lora.py --data data/train.jsonl --out adapters/malo-1b
ollama create malo-1b -f adapters/malo-1b/Modelfile
python benchmark.py --models llama3.2:1b malo-1b --scenario contended --jitter 0.4 --seeds 30
```

See `finetune/README.md`. The training script is written but **has never been
executed** — there was no GPU available where it was authored.

---

## Status

Done: physics, P2P protocol, parser firewall, control arm, LP oracle and the
bound-invariant test, 6 scenario presets and 5 topologies to 32+ nodes, data
provenance, adversarial behaviours, edge feasibility metrics, prompt ablation,
oracle-labelled dataset generation, paired benchmark sweep. 32 tests passing.

Not done:

1. **No live model has ever run this.** Everything downstream of the HTTP
   response is exercised by a mock reproducing observed 1B failure modes, but
   real compliance, latency and allocation quality are unmeasured.
2. **No measured data.** The loaders exist; nobody has pointed them at a real
   dataset yet.
3. **LoRA training never executed** (no GPU).
4. **Energy per block is an assumption**, not a measurement.
5. **No AC power flow** — energy-only, no voltage or frequency constraints. Grid
   engineers will ask.
6. **Two-hop routing is out of scope** for both agents and oracle, so relay
   through a neighbour is unavailable even where it would help.

---

## CLI reference

| Flag | Default | Meaning |
|------|---------|---------|
| `--scenario` | `brief` | `brief`, `contended`, or `street`/`block`/`feeder`/`district` |
| `--nodes` / `--topology` | — | override generated presets (`mesh`, `ring`, `line`, `geometric`, `star`) |
| `--start-hour` | `15` | 15:00 is where allocation is genuinely contested |
| `--offline` | off | heuristic control arm; never touches the network |
| `--jitter` | `0.0` | randomise generation/demand ±fraction |
| `--adversary` | `honest` | `misreport`, `freerider`, `hoarder`, `dropout` |
| `--adversary-fraction` | `0.0` | share of nodes made adversarial |
| `--prompt-style` | `full` | ablation: `no-bounds`, `no-reason`, `terse` |
| `--trace PATH` | — | JSONL of every prompt/response, for fine-tuning |
| `--model` / `--url` | `llama3.2:1b` | Ollama model and endpoint |
| `--blocks` / `--seed` | `3` / `7` | trading blocks, RNG seed |
| `--plot` | off | metrics PNG (needs matplotlib) |

Environment overrides: `OLLAMA_URL`, `MALO_MODEL`.

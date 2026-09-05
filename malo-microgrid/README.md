# MALO — Multi-Agent Local-LLM Energy Micro-grid

A testbed for one research question:

> Can ultra-compressed local language models (1B–3B parameters, running entirely
> on cheap edge hardware) autonomously solve a real-time, physically constrained
> resource-allocation problem through **decentralized negotiation alone** — no
> central optimizer, no solver, no cloud?

Three houses on a mesh network negotiate electricity in natural language. A
physics engine then settles the kWh their conversation actually agreed on,
subject to line capacity, transmission loss, battery limits and credit budgets.

Everything runs offline against [Ollama](https://ollama.com). No API keys, no
network egress, no central orchestrator.

---

## Quick start

```bash
pip install -r requirements.txt

# 1. deterministic control arm — no model needed, runs in ~1 second
python microgrid_sim.py --offline

# 2. the real thing
ollama serve
ollama pull llama3.2:1b
python microgrid_sim.py

# 3. the benchmark that produces an actual result
python benchmark.py --models llama3.2:1b --scenario contended --jitter 0.4 --seeds 20

# 4. tests (no pytest required)
python test_microgrid.py
```

| File | |
|------|---|
| `microgrid_sim.py` | agents, negotiation protocol, physics, parser firewall |
| `oracle.py` | LP optimal-allocation bound — the denominator for every result |
| `benchmark.py` | paired multi-seed sweep with confidence intervals |
| `test_microgrid.py` | 19 tests |

The console prints the whole negotiation as it unfolds — every request, offer,
counter-bid and settlement, with a marker showing whether each number came from
the model or from the deterministic fallback.

---

## The scenario

| Node | Role | Block-1 state | Notes |
|------|------|---------------|-------|
| **A** | Solar prosumer | **+5.0 kWh** surplus | unsold surplus is *curtailed* — wasted |
| **B** | EV consumer | **−8.0 kWh** deficit | shortfall is bought from the utility at 0.45 tok/kWh |
| **C** | Battery balancer | **0.0 kWh** net, 10 kWh pack | buys cheap, stores, resells when scarce |

Topology is a triangle with real line limits:

```
        A ──── 5.0 kWh cap, 2% loss ──── B
         \                              /
    5.0 kWh cap, 2% loss    6.0 kWh cap, 3% loss
           \                          /
                        C
```

The A–B line is deliberately too small (5 kWh) to serve B's 8 kWh demand on its
own. **No bilateral trade can clear the block** — the agents have to discover a
multi-party allocation across both sellers, under line constraints, by talking.
That is the whole point of the benchmark.

Solar decays and the pack drains across blocks, so scarcity *rises* over the
run. Price discovery therefore has to be dynamic, not a fixed lookup.

---

## Protocol (one trading block)

| Step | What happens |
|------|--------------|
| **4.1 Internal assessment** | Every node reads its own meter and sets an objective: `intent`, `target_kwh`, `reservation_price`. |
| **4.2 P2P negotiation** | Any node in deficit broadcasts a `REQUEST` to its wired neighbours. Sellers reply with priced `OFFER`s. The buyer issues one `COUNTER` bid; sellers `ACCEPT` or `REVISE`. The buyer then allocates its purchase across sellers. |
| **4.3 Settlement** | Physics applies the agreed kWh — line capacity, loss, battery limits and credits are all re-checked. Negotiation is advisory; physics is final. |
| **4.4 Surplus absorption** | A node still holding unsold solar advertises it as *distressed* to its neighbours. Storage nodes decide on their own whether to buy it. This is what drives curtailment toward zero. |

### Why there is no central orchestrator

The constraint is enforced structurally, not by convention:

* `MicroGrid` owns topology and line physics only. It never ranks an offer,
  picks a counterparty or clears a market. It is copper, not a broker.
* `run_trading_block()` is a simulation clock plus a message courier. It carries
  `Message` objects along edges in the order agents initiate them.
* Every allocation decision happens inside a `GridAgent`, from that agent's own
  private state plus messages its neighbours *chose to disclose*. An agent can
  never read another agent's `NodeState` — a test asserts this on the method
  signatures.
* Buyers are discovered from physics (any node carrying a deficit), never
  assigned by a coordinator. Assessment order is randomised each block so no
  node holds structural priority.

---

## Measuring against the optimum

`allocation_efficiency = locally-served demand ÷ the most any method could have
served`, where the denominator comes from an exact linear program in
`oracle.py` solved on the same state the agents were handed. Without it, "the
agents served 93% of demand" is unfalsifiable — 93% of what?

The LP is lexicographic, in physical priority order: **serve demand** (a fossil
peaker not started), then **waste no clean energy** (curtailment destroys it
outright), then **waste no line losses**. It is restricted to the same action
space the agents have — single-hop trades between wired neighbours — so it is a
tight bound on what the negotiation could have reached, not a fantasy one.

A test asserts the simulation can never beat it. If it ever does, the bound is
broken and every efficiency figure in the repo is meaningless.

### Finding: the brief's 3-node scenario cannot measure anything

The first thing the oracle revealed is that **the reference scenario is too
easy**. It has one buyer, and with a single buyer cheapest-first greedy
allocation is provably optimal — there is no competitor, so no ordering
decision can be got wrong. Measured: the heuristic control arm scores **98.7%**
of optimum, hitting exactly 100% on 2 of 3 blocks. No method can demonstrate an
advantage in a 1.3% window that is smaller than a small model's sampling noise.

Hence `--scenario contended`: 6 nodes, 2 prosumers, 3 loads, 1 storage hub, with
buyers competing for partially overlapping sellers. `B` and `F` each reach only
one prosumer, so supply claimed by the well-connected node `E` strands them.
The order in which buyers claim supply now changes the system outcome, and
sequential greedy has no way to see that coming.

Measured baseline, `--scenario contended --jitter 0.4`, 8 instances:

```
  allocation efficiency  [%]
    heuristic      92.45  ±  4.12   (n=8)
```

**That 7.5-point gap is the target.** If local LLM negotiation is worth
anything, it closes some of it. If it does not, that is a real negative result
about 1B-class models on constrained allocation — publish it either way.

---

## The hallucination firewall

A 1B model *will* return `"price": "about twelve cents"` and *will* try to sell
40 kWh it does not have. Rather than pretend otherwise, the system measures it.
Four layers, each degradation counted in telemetry:

1. **Fenced-block extraction** — prefers ` ```json `, then any balanced `{…}`,
   taking the *last* parseable object (small models restate their corrected
   answer at the end).
2. **JSON repair** — trailing commas, bare keys, single quotes, `//` comments,
   Python `True`/`None` literals.
3. **Numeric coercion** — `"5.0 kWh"`, `"$0.12"`, `"0,12"`, `"four"`,
   `"about 3–4 kWh"` (→ midpoint), lists, nulls.
4. **Regex salvage** — hunts `key … number` in prose when there is no JSON at all.
5. **Deterministic fallback** — a real policy, not a stub: greedy merit-order
   allocation for buyers, scarcity-priced offers for sellers, split-the-
   difference haggling. `--offline` runs this alone as the **control arm**.

On top of that, every number the model produces passes through physics clamps
before it can become a contract, and each clamp is counted.

---

## Output

Console transcript, plus a summary block. Here is a real run of the
deterministic control arm (`--offline --blocks 3`), which is the baseline every
LLM run is measured against:

```
blk  hr     gen     dem  traded   loss   price  utility  curtail  local%
------------------------------------------------------------------------
  1  13    5.00    8.00    7.81   0.19   0.195     0.19     0.00   97.6%
  2  14    3.50    6.00    5.13   0.12   0.195     0.87     0.00   85.5%
  3  15    1.50    4.50    1.47   0.03   0.195     3.03     0.00   32.7%

  PHYSICAL OUTCOME
    total demand              :    18.50 kWh
    peer-to-peer delivered    :    14.41 kWh
    served locally            :     77.9 %
    utility (peaker) import   :     4.09 kWh   <- the number to drive to zero
    clean energy curtailed    :     0.00 kWh   <- the other number to drive to zero
    solar self-consumption    :    100.0 %
```

Block 3 is the hard one: solar has decayed to 1.5 kWh and C's pack has hit its
reserve floor, so 67% of demand falls through to the utility. Beating that
number is the benchmark.

With a live model the run also reports an `LLM BEHAVIOUR` section — generation
calls, clean JSON blocks, regex salvages, fallbacks, physics clamps, structured
compliance, decision autonomy and mean latency per call. (In `--offline` mode
those rows are all zero by construction.)

Two headline metrics per run:

* **`utility_import_kwh`** — kWh the agents *failed* to source locally, i.e. the
  fossil peaker they did not displace. Drive to zero.
* **`curtailed_kwh`** — clean energy wasted because nobody bought it. Drive to zero.

And two research metrics:

* **structured compliance** — share of replies that were directly parseable JSON.
  This is the number that decides which models are deployable at 1B.
* **decision autonomy** — share of decisions actually made by the model rather
  than the fallback rule. A run with high autonomy *and* low utility import is
  the result that matters; high autonomy with bad allocations is a negative result
  worth just as much.

CSVs land in `runs/`: `block_metrics.csv`, `trade_ledger.csv`, `llm_calls.csv`
(per-call latency, parse mode, errors — the raw material for a model comparison).

---

## Suggested experiments

```bash
# Does model size buy better allocations, or just better JSON?
for m in llama3.2:1b qwen2.5:3b phi3:mini; do
  python microgrid_sim.py --model "$m" --blocks 5 --outdir "runs/$m"
done

# Control arm: how much value do the LLMs add over a greedy heuristic at all?
python microgrid_sim.py --offline --blocks 5 --outdir runs/heuristic
```

The interesting comparison is **LLM run vs. `--offline` control arm** on
`utility_import_kwh` and `curtailed_kwh`. If the LLMs cannot beat greedy
merit-order dispatch, that is the honest finding — and the harness is built to
report it rather than hide it.

## Status

Done:

* Physics, P2P negotiation protocol, parser firewall, deterministic control arm.
* LP optimality oracle + the efficiency metric, with the bound-invariant test.
* Contended scenario, scenario jitter, paired benchmark sweep with CIs.
* JSONL trace of every prompt/response pair (`--trace`) — Phase-4 training data
  accumulates from ordinary runs, including the failures, which are the
  interesting half.
* 19 tests passing.

Not done — in rough priority order:

1. **A live model has never run this.** Everything downstream of the HTTP
   response is exercised by a `MockModel` reproducing observed 1B failure modes,
   but real compliance rates, latency and allocation quality are unmeasured.
   This is the next step and it needs a machine with Ollama.
2. **Real data.** Solar and demand curves are hand-built. NREL PVWatts, an EV
   charging dataset and a real TOU tariff would make results defensible.
3. **Scale and topology.** 3 and 6 nodes; decentralization claims want 20–50
   nodes and varied topologies (line, ring, random geometric).
4. **Edge feasibility.** Tokens and wall-clock per decision are logged, but not
   joules, and there is no check that a negotiation round fits inside a real
   market interval on Pi-class hardware.
5. **Robustness.** No adversarial agent (misreporting capacity), no node
   dropout mid-negotiation, no Byzantine case — all of which the grid-resilience
   pitch implicitly claims.
6. **Fine-tuning loop.** Traces are captured; the Unsloth training script and
   the base-vs-LoRA eval are not written.
7. **Prompt ablation.** Which prompt features actually buy compliance at 1B
   (pre-computed bounds, reasoning-before-JSON, `format:json`) is untested.

## CLI reference

| Flag | Default | Meaning |
|------|---------|---------|
| `--model` | `llama3.2:1b` | Ollama model tag |
| `--url` | `http://localhost:11434/api/generate` | Ollama endpoint |
| `--blocks` | `3` | number of trading blocks |
| `--scenario` | `brief` | `brief` (3 nodes, single buyer) or `contended` (6 nodes, real contention) |
| `--jitter` | `0.0` | randomise generation/demand by ±fraction; use with `--seed` to sample instances |
| `--trace PATH` | — | append every prompt/response pair to JSONL for fine-tuning |
| `--battery-soc` | `6.0` | node C's initial stored energy (net balance stays 0) |
| `--offline` | off | heuristic control arm; never touches the network |
| `--timeout` | `90` | per-call timeout (a 1B model on a Pi 4 is slow) |
| `--seed` | `7` | RNG seed for agent ordering |
| `--outdir` | `runs` | CSV/PNG destination |
| `--plot` | off | render a metrics PNG (needs matplotlib) |
| `--no-export` / `--no-color` | off | skip CSVs / disable ANSI |

Environment overrides: `OLLAMA_URL`, `MALO_MODEL`.

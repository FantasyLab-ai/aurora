#!/usr/bin/env python3
"""
MALO — Multi-Agent LLM Optimizer for a peer-to-peer energy micro-grid.
=====================================================================

Research question
-----------------
Can ultra-compressed local language models (1B-3B params, running on a $35 SBC)
autonomously solve a real-time constrained resource-allocation problem through
*decentralized negotiation only* — no central optimizer, no cloud, no solver?

This script is the reference testbed for that question. Three houses on a mesh
negotiate electricity in natural language; the physics engine then settles the
kWh that the conversation actually agreed on.

Architecture constraint (hard rule)
-----------------------------------
There is NO central orchestrator. Concretely, in this file:

  * `MicroGrid` owns physics and topology ONLY (line capacity, losses, who is
    wired to whom). It never chooses an allocation, never ranks offers, never
    clears a market. It is the copper, not a broker.
  * `run_trading_block()` is a *simulation clock plus a message courier*. It
    advances time and hands messages along edges. It makes no trade decisions.
  * Every allocation decision is made inside a `GridAgent` from that agent's
    own local state plus messages its neighbours chose to disclose. An agent
    can NEVER read another agent's `NodeState` — peers exchange `Message`
    objects and nothing else. This is enforced structurally, not by convention.
  * `Telemetry` is passive observability (a logbook). It has no authority.

Negotiation sequence per trading block
--------------------------------------
  4.1  Internal assessment   — each node reads its own physics, forms an objective.
  4.2  Decentralized loop    — B broadcasts a REQUEST to its neighbours;
                               A and C reply with OFFERs (price tokens/kWh);
                               B counter-bids; sellers ACCEPT or REVISE;
                               B issues its final allocation.
  4.3  Settlement            — physics applies the agreed kWh subject to line
                               capacity, transmission losses and battery limits.
  4.4  Surplus absorption    — C bilaterally bids for A's unsold solar so it is
                               stored rather than curtailed (still pure P2P).

Every LLM call has a deterministic fallback. A 1B model *will* emit
`"price": "about twelve cents"` or invent 40 kWh it does not have. The parser
salvages what it can, the clamps enforce physics, and the heuristic takes over
when the text is unusable — all of it counted in the telemetry so hallucination
cost is a measured quantity, not an anecdote.

Usage
-----
    ollama serve && ollama pull llama3.2:1b
    python microgrid_sim.py                       # full LLM negotiation
    python microgrid_sim.py --offline             # heuristics only, no Ollama
    python microgrid_sim.py --model qwen2.5:3b --blocks 5 --plot

Stack: Python 3.10+, requests, networkx, (optional) pandas, matplotlib.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Iterable, Optional

try:
    import requests
except ImportError:  # pragma: no cover - dependency guard
    print("FATAL: `requests` is required.  pip install requests networkx", file=sys.stderr)
    raise

try:
    import networkx as nx
except ImportError:  # pragma: no cover - dependency guard
    print("FATAL: `networkx` is required.  pip install requests networkx", file=sys.stderr)
    raise

try:
    # The optimal-allocation oracle. Optional at import time so the simulation
    # still runs on a machine without it, just without an optimality gap.
    from oracle import oracle_for_block
except ImportError:  # pragma: no cover
    oracle_for_block = None


# =============================================================================
# SECTION 0 — Configuration constants
# =============================================================================

OLLAMA_URL_DEFAULT = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MODEL_DEFAULT = os.environ.get("MALO_MODEL", "llama3.2:1b")

# Generation options tuned for tiny models doing arithmetic: near-greedy decoding,
# short outputs (a 1B model rambles itself into hallucination past ~250 tokens).
LLM_OPTIONS = {
    "temperature": 0.15,   # low but non-zero: some price dispersion is realistic
    "top_p": 0.9,
    "num_predict": 260,    # hard ceiling on reply length
    "repeat_penalty": 1.1,
}
LLM_TIMEOUT_S = 90         # a 1B model on a Pi 4 can take a while; be patient
LLM_RETRIES = 2            # transient socket errors only; not a hallucination retry

# --- Market rules (physics-agnostic economic bounds, known to every agent) ---
PRICE_FLOOR = 0.05         # tokens/kWh — below this, selling is irrational
PRICE_CEILING = 0.45       # tokens/kWh — grid-import reference price ("peaker" price)
GRID_IMPORT_PRICE = 0.45   # what B pays the utility for anything it fails to source locally

# --- Battery physics for the balancer node ---
BATTERY_RESERVE_KWH = 1.0      # never discharge below this (backup floor)
BATTERY_CHARGE_EFF = 0.95      # kWh stored per kWh delivered to the pack
BATTERY_DISCHARGE_EFF = 0.95   # kWh exported per kWh pulled from the pack
BATTERY_MAX_RATE_KWH = 5.0     # per trading block (C-rate limit)

# --- Negotiation shape ---
HAGGLE_ROUNDS = 2          # round 1 = offer, round 2 = counter-bid + accept/revise
EPS = 1e-6                 # float tolerance for energy-conservation checks


# =============================================================================
# SECTION 1 — Console logging (so the operator can watch negotiation unfold)
# =============================================================================

class Console:
    """Human-readable transcript of the negotiation. Pure output, zero authority."""

    COLORS = {
        "A": "\033[93m",  # yellow  — solar
        "B": "\033[91m",  # red     — demand
        "C": "\033[94m",  # blue    — storage
        "D": "\033[33m",  # dim yellow — second solar
        "E": "\033[31m",  # dim red    — second demand
        "F": "\033[35m",  # magenta    — third demand
        "SYS": "\033[90m",
        "OK": "\033[92m",
        "WARN": "\033[95m",
        "RESET": "\033[0m",
    }

    def __init__(self, color: bool = True, quiet: bool = False) -> None:
        # Disable ANSI when piped to a file or on a dumb terminal.
        self.color = color and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
        # Quiet mode suppresses the transcript entirely — used by the benchmark
        # sweep, where hundreds of blocks would bury the aggregate result.
        self.quiet = quiet

    def _print(self, *args) -> None:
        if not self.quiet:
            print(*args)

    def _c(self, key: str, text: str) -> str:
        if not self.color:
            return text
        return f"{self.COLORS.get(key, '')}{text}{self.COLORS['RESET']}"

    def block_header(self, index: int, hour: int, total: int) -> None:
        bar = "=" * 78
        self._print("\n" + self._c("SYS", bar))
        self._print(self._c("SYS", f"  TRADING BLOCK {index}/{total}   —   simulated hour {hour:02d}:00"))
        self._print(self._c("SYS", bar))

    def phase(self, title: str) -> None:
        self._print("\n" + self._c("SYS", f"--- {title} " + "-" * max(0, 73 - len(title))))

    def agent_say(self, node_id: str, text: str, source: str = "llm") -> None:
        """Print an agent's own words. `source` marks LLM output vs heuristic."""
        tag = f"[{node_id}]"
        badge = "" if source == "llm" else self._c("WARN", f" ({source})")
        wrapped = self._wrap(text, indent=6)
        self._print(f"  {self._c(node_id, tag)}{badge} {wrapped.lstrip()}")

    def wire(self, sender: str, recipient: str, kind: str, summary: str) -> None:
        """Render a message physically crossing a line between two nodes."""
        arrow = f"{self._c(sender, sender)} --> {self._c(recipient, recipient)}"
        self._print(f"    {arrow}  {kind:<8} {summary}")

    def note(self, text: str) -> None:
        self._print(self._c("SYS", f"    · {text}"))

    def warn(self, text: str) -> None:
        self._print(self._c("WARN", f"    ! {text}"))

    def ok(self, text: str) -> None:
        self._print(self._c("OK", f"    ✓ {text}"))

    @staticmethod
    def _wrap(text: str, indent: int = 6, width: int = 96) -> str:
        """Cheap word wrap — keeps 1B-model rambling readable in a terminal."""
        words, lines, cur = text.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 > width:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        lines.append(cur)
        pad = " " * indent
        return ("\n" + pad).join(lines)


# =============================================================================
# SECTION 2 — Ollama client (robust JSON payload handler)
# =============================================================================

@dataclass
class LLMResult:
    """Outcome of one generation call. `ok=False` means the caller must fall back."""
    text: str
    ok: bool
    latency_s: float
    error: str = ""


class OllamaClient:
    """
    Minimal, dependency-light client for POST http://localhost:11434/api/generate.

    Deliberately NOT using `"format": "json"`. The brief wants the model to
    reason in prose and then emit a JSON block inside that prose — forcing
    strict JSON mode would suppress the reasoning trace we want to study, and
    would also hide exactly the parse-failure behaviour this research measures.
    """

    def __init__(self, url: str = OLLAMA_URL_DEFAULT, model: str = MODEL_DEFAULT,
                 timeout: int = LLM_TIMEOUT_S, offline: bool = False,
                 console: Optional[Console] = None) -> None:
        self.url = url
        self.model = model
        self.timeout = timeout
        self.offline = offline            # hard switch: never touch the network
        self.console = console or Console()
        self.available: Optional[bool] = None if not offline else False

    # -- health ---------------------------------------------------------------
    def probe(self) -> bool:
        """One cheap round-trip so we fail loudly at startup, not mid-negotiation."""
        if self.offline:
            self.available = False
            return False
        try:
            tags_url = self.url.replace("/api/generate", "/api/tags")
            resp = requests.get(tags_url, timeout=5)
            resp.raise_for_status()
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            self.available = True
            if models and not any(m.split(":")[0] == self.model.split(":")[0] for m in models):
                self.console.warn(
                    f"model '{self.model}' not in local Ollama library {models}; "
                    f"run:  ollama pull {self.model}")
            return True
        except Exception as exc:  # noqa: BLE001 - any failure means "no local model"
            self.available = False
            self.console.warn(f"Ollama unreachable at {self.url} ({type(exc).__name__}); "
                              f"running in deterministic-fallback mode.")
            return False

    # -- generation -----------------------------------------------------------
    def generate(self, system: str, prompt: str) -> LLMResult:
        """
        Send one prompt. Returns LLMResult; never raises. A failed call is a
        normal, expected event in this system — the agent simply falls back.
        """
        if self.offline or self.available is False:
            return LLMResult("", False, 0.0, "offline")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,        # single JSON document back, not an NDJSON stream
            "options": dict(LLM_OPTIONS),
        }

        last_err = ""
        for attempt in range(1, LLM_RETRIES + 1):
            started = time.time()
            try:
                resp = requests.post(self.url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                body = resp.json()
                text = (body.get("response") or "").strip()
                elapsed = time.time() - started
                if not text:
                    last_err = "empty response field"
                    continue
                return LLMResult(text, True, elapsed)
            except requests.exceptions.Timeout:
                last_err = f"timeout after {self.timeout}s"
            except requests.exceptions.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            except ValueError as exc:  # malformed JSON envelope from the daemon
                last_err = f"bad JSON envelope: {exc}"
            if attempt < LLM_RETRIES:
                time.sleep(0.5 * attempt)  # brief linear backoff on transport errors

        return LLMResult("", False, time.time() - started, last_err)


# =============================================================================
# SECTION 3 — Defensive parsing (the 1B-hallucination firewall)
# =============================================================================
#
# A 3B model returns clean JSON perhaps 90% of the time; a 1B model far less.
# Observed failure modes this layer is built to survive:
#   * fenced block with a trailing comma or a stray ``` inside
#   * prose around the JSON, or two JSON blocks (the second is the real answer)
#   * single quotes instead of double, Python True/None literals
#   * numbers as words ("four kWh") or with units ("0.12 tokens/kWh", "5kWh")
#   * plainly impossible values (sell 40 kWh from a 5 kWh surplus)
#
# Order of defence: fenced block -> balanced-brace scan -> regex key/value
# salvage -> deterministic heuristic. Each downgrade is counted in Telemetry.

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "half": 0.5, "none": 0, "nothing": 0, "all": -1,  # -1 = sentinel "everything"
}


def _repair_jsonish(blob: str) -> str:
    """Best-effort repair of near-JSON emitted by small models."""
    s = blob.strip()
    s = re.sub(r"//[^\n]*", "", s)                 # // line comments
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)    # /* block comments */
    s = re.sub(r",\s*([}\]])", r"\1", s)           # trailing commas
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\b(None|nan|NaN)\b", "null", s)
    # Bare keys -> quoted keys:  {price: 0.1}  ->  {"price": 0.1}
    s = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)", r'\1"\2"\3', s)
    # Single-quoted strings -> double-quoted (only when no double quotes present)
    if '"' not in s and "'" in s:
        s = s.replace("'", '"')
    return s


def _balanced_objects(text: str) -> list[str]:
    """Yield every top-level {...} substring, quote- and escape-aware."""
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start:i + 1])
                start = -1
            elif depth < 0:
                depth = 0  # stray closing brace from a confused model
    return out


def extract_json_block(text: str) -> Optional[dict]:
    """
    Pull the decision object out of a mixed prose+JSON reply.

    Strategy: prefer an explicitly fenced ```json block; otherwise scan for
    balanced objects and take the LAST parseable one — small models tend to
    restate their answer at the end, and the final statement is the decision.
    """
    if not text:
        return None

    candidates: list[str] = []
    # 1) fenced blocks, ```json first then any fence
    for pattern in (r"```json\s*(.*?)```", r"```\s*(\{.*?\})\s*```"):
        candidates.extend(re.findall(pattern, text, flags=re.S | re.I))
    # 2) any balanced object anywhere in the text
    candidates.extend(_balanced_objects(text))

    for raw in reversed(candidates):  # last statement wins
        for attempt in (raw, _repair_jsonish(raw)):
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, dict) and parsed:
                    return parsed
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def coerce_number(value: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Turn whatever the model produced into a float, or `default`.

    Handles: 5, "5", "5.0 kWh", "$0.12", "0,12", "four", "about 3-4 kWh"
    (a range collapses to its midpoint), "12%" -> 0.12 is NOT assumed —
    percent signs are stripped and the number kept as-is, since unit intent
    is ambiguous and the caller clamps anyway.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default              # True is not a quantity
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)) and value:
        return coerce_number(value[0], default)
    if not isinstance(value, str):
        return default

    s = value.strip().lower().replace(",", ".").replace("$", "").replace("%", "")
    for word, num in NUMBER_WORDS.items():
        if re.fullmatch(rf"\W*{word}\W*", s):
            return float(num)

    # A stated range collapses to its midpoint: "about 3-4 kWh" -> 3.5.
    # Matched before the generic scan so the "-" is read as a range separator
    # rather than as the sign of a negative number.
    span = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to|–|—)\s*(\d+(?:\.\d+)?)", s)
    if span:
        return (float(span.group(1)) + float(span.group(2))) / 2.0

    nums = re.findall(r"-?\d+(?:\.\d+)?", s)
    if not nums:
        return default
    return float(nums[0])


def salvage_by_regex(text: str, keys: Iterable[str]) -> dict:
    """
    Last resort before the heuristic: hunt for `key ... number` anywhere in the
    prose, JSON or not. Recovers replies like:
        "I can offer 4 kWh at a price of 0.18 per kWh."
    """
    found: dict[str, float] = {}
    for key in keys:
        pattern = rf"[\"']?{re.escape(key)}[\"']?\s*(?:[:=]|is|of|:)?\s*[\"']?(-?[\d.]+)"
        m = re.search(pattern, text, flags=re.I)
        if m:
            val = coerce_number(m.group(1))
            if val is not None:
                found[key] = val
    return found


def clamp(value: float, low: float, high: float) -> float:
    """Physics guard-rail. Every LLM number passes through this before settlement."""
    return max(low, min(high, value))


# =============================================================================
# SECTION 4 — Telemetry (passive logbook; no decision authority)
# =============================================================================

@dataclass
class Telemetry:
    """Research metrics. Counts what the LLMs got right and what they cost us."""
    llm_calls: int = 0
    llm_failures: int = 0          # transport-level (unreachable / timeout)
    json_ok: int = 0               # clean structured block found
    json_salvaged: int = 0         # recovered by regex from prose
    json_lost: int = 0             # unusable -> deterministic heuristic ran
    clamped_values: int = 0        # model proposed physically impossible numbers
    latency_s: float = 0.0
    events: list[dict] = field(default_factory=list)
    trace_path: Optional[str] = None   # JSONL of (system, prompt, response) triples

    def record_call(self, node: str, task: str, res: LLMResult, parse_mode: str) -> None:
        self.llm_calls += 1
        self.latency_s += res.latency_s
        # `offline` is a deliberate mode, not a fault — don't inflate the
        # failure count with it, or the control arm looks like a broken run.
        if not res.ok and res.error != "offline":
            self.llm_failures += 1
        if parse_mode == "json":
            self.json_ok += 1
        elif parse_mode == "salvage":
            self.json_salvaged += 1
        else:
            self.json_lost += 1
        self.events.append({
            "node": node, "task": task, "ok": res.ok,
            "parse": parse_mode, "latency_s": round(res.latency_s, 3),
            "error": res.error,
        })

    def note_clamp(self) -> None:
        self.clamped_values += 1

    def trace(self, node: str, task: str, system: str, prompt: str,
              response: str, parse_mode: str, decision: dict) -> None:
        """
        Append one negotiation turn to a JSONL trace.

        This is the raw material for Phase 4 (fine-tuning). Every turn is stored
        with the prompt exactly as the model saw it, so a LoRA dataset can be
        distilled from real runs — including the failures, which are the
        interesting half: a turn where a 1B model produced garbage and a bigger
        model (or the fallback rule) produced a valid decision is a training
        pair. Written as it happens so a crashed or interrupted run still yields
        usable data.
        """
        if not self.trace_path:
            return
        record = {"node": node, "task": task, "system": system, "prompt": prompt,
                  "response": response, "parse": parse_mode, "decision": decision}
        with open(self.trace_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")

    @property
    def structured_compliance(self) -> float:
        """Fraction of successful calls that produced directly parseable JSON."""
        usable = self.json_ok + self.json_salvaged + self.json_lost
        return (self.json_ok / usable) if usable else 0.0

    @property
    def autonomy_rate(self) -> float:
        """Fraction of decisions actually made by the model rather than the fallback."""
        usable = self.json_ok + self.json_salvaged + self.json_lost
        return ((self.json_ok + self.json_salvaged) / usable) if usable else 0.0


# =============================================================================
# SECTION 5 — Physical node state
# =============================================================================

@dataclass
class NodeState:
    """
    The private physical reality of one house. PRIVATE is load-bearing: no other
    agent may read this object. Peers learn only what is put into a `Message`.
    """
    node_id: str
    role: str
    net_kwh: float                     # + = exportable surplus, - = unmet demand
    battery_kwh: float = 0.0           # stored energy
    battery_capacity: float = 0.0      # 0 => node has no storage
    credits: float = 10.0              # trading tokens
    reservation_price: float = PRICE_FLOOR

    # Per-block bookkeeping, reset by `begin_block`
    generated: float = 0.0
    consumed: float = 0.0
    imported: float = 0.0
    exported: float = 0.0
    curtailed: float = 0.0             # surplus wasted because nobody took it
    grid_import: float = 0.0           # bought from the utility / peaker plant

    # -- derived physical limits ---------------------------------------------
    @property
    def has_battery(self) -> bool:
        return self.battery_capacity > 0.0

    @property
    def deficit(self) -> float:
        """kWh still needed this block (always >= 0)."""
        return max(0.0, -self.net_kwh)

    @property
    def surplus(self) -> float:
        """Immediately available kWh from generation (always >= 0)."""
        return max(0.0, self.net_kwh)

    def dischargeable(self) -> float:
        """kWh the battery can deliver to the wires this block."""
        if not self.has_battery:
            return 0.0
        usable = max(0.0, self.battery_kwh - BATTERY_RESERVE_KWH)
        return min(usable * BATTERY_DISCHARGE_EFF, BATTERY_MAX_RATE_KWH)

    def chargeable(self) -> float:
        """kWh the battery can absorb from the wires this block."""
        if not self.has_battery:
            return 0.0
        headroom = max(0.0, self.battery_capacity - self.battery_kwh)
        return min(headroom / BATTERY_CHARGE_EFF, BATTERY_MAX_RATE_KWH)

    def max_export(self) -> float:
        """Total kWh this node could physically sell this block."""
        return self.surplus + self.dischargeable()

    def begin_block(self) -> None:
        self.generated = self.consumed = 0.0
        self.imported = self.exported = 0.0
        self.curtailed = self.grid_import = 0.0

    def snapshot(self) -> dict:
        """Public-safe summary for logging/metrics (still never handed to a peer)."""
        return {
            "node": self.node_id, "role": self.role,
            "net_kwh": round(self.net_kwh, 3),
            "battery_kwh": round(self.battery_kwh, 3),
            "credits": round(self.credits, 3),
        }


# =============================================================================
# SECTION 6 — Topology and physics (copper only — makes no decisions)
# =============================================================================

@dataclass
class Message:
    """
    The ONLY channel between agents. If a fact is not in here, the recipient
    does not know it. Messages travel along physical edges; there is no bus,
    no broker and no broadcast medium beyond a node's own neighbourhood.
    """
    sender: str
    recipient: str
    kind: str                       # REQUEST | OFFER | COUNTER | ACCEPT | REVISE | AWARD | BID
    payload: dict = field(default_factory=dict)

    def summary(self) -> str:
        bits = []
        for key in ("kwh", "price", "max_kwh", "need_kwh"):
            if key in self.payload and self.payload[key] is not None:
                unit = "kWh" if key.endswith("kwh") else "tok/kWh"
                bits.append(f"{self.payload[key]:.2f} {unit}")
        note = self.payload.get("note", "")
        return " @ ".join(bits) + (f"   \"{note[:56]}\"" if note else "")


class MicroGrid:
    """
    networkx topology + line physics. It holds capacity and losses, and refuses
    transfers that violate them. It never selects a counterparty or a price.
    """

    def __init__(self) -> None:
        self.g = nx.Graph()
        # Triangle mesh. Line capacity is per trading block; loss is resistive.
        # A-B is deliberately too small to serve B alone (5 kWh line, 8 kWh demand)
        # so the agents MUST discover a multi-party allocation to clear the block.
        self.g.add_node("A", role="solar_prosumer")
        self.g.add_node("B", role="ev_consumer")
        self.g.add_node("C", role="battery_balancer")
        self.g.add_edge("A", "B", capacity_kwh=5.0, loss=0.02)
        self.g.add_edge("A", "C", capacity_kwh=5.0, loss=0.02)
        self.g.add_edge("B", "C", capacity_kwh=6.0, loss=0.03)
        self._used: dict[frozenset, float] = {}

    def begin_block(self) -> None:
        """Thermal/contractual line usage resets each trading block."""
        self._used = {frozenset(e): 0.0 for e in self.g.edges()}

    def neighbours(self, node_id: str) -> list[str]:
        """Who this node can physically talk to. Defines its negotiation horizon."""
        return sorted(self.g.neighbors(node_id))

    def linked(self, u: str, v: str) -> bool:
        return self.g.has_edge(u, v)

    def loss(self, u: str, v: str) -> float:
        return float(self.g[u][v]["loss"]) if self.linked(u, v) else 1.0

    def spare_capacity(self, u: str, v: str) -> float:
        if not self.linked(u, v):
            return 0.0
        cap = float(self.g[u][v]["capacity_kwh"])
        return max(0.0, cap - self._used.get(frozenset((u, v)), 0.0))

    def consume_capacity(self, u: str, v: str, kwh: float) -> None:
        key = frozenset((u, v))
        self._used[key] = self._used.get(key, 0.0) + kwh

    def deliver(self, msg: Message, console: Console) -> bool:
        """
        Courier function: a message crosses only an existing physical link.
        This is transport, not orchestration — nothing here inspects payload
        semantics or decides anything on an agent's behalf.
        """
        if not self.linked(msg.sender, msg.recipient):
            console.warn(f"{msg.sender}->{msg.recipient} has no line; message dropped")
            return False
        console.wire(msg.sender, msg.recipient, msg.kind, msg.summary())
        return True


# =============================================================================
# SECTION 7 — Prompt engineering for 1B-class models
# =============================================================================
#
# Rules that measurably help tiny models with numeric reasoning:
#   1. Give the role and the *numbers* up front, pre-computed. Never ask a 1B
#      model to derive a bound it could get wrong — hand it the bound.
#   2. Demand short prose reasoning FIRST, then the JSON. Reasoning-after-answer
#      is decoration; reasoning-before-answer measurably improves the number.
#   3. Show the exact JSON schema with realistic example values, keys only,
#      no nesting deeper than one level.
#   4. State the legal numeric range for every field, inline.
#   5. Forbid units inside JSON values — units are the #1 source of parse loss.

SCHEMA_RULES = (
    "OUTPUT RULES (follow exactly):\n"
    "1. First write at most 2 short sentences of reasoning about the numbers.\n"
    "2. Then output ONE fenced JSON block, nothing after it.\n"
    "3. JSON values must be BARE NUMBERS: 0.18 not \"0.18 tokens/kWh\".\n"
    "4. Never invent energy you do not have. Stay inside the stated limits.\n"
)

# Keyed by ROLE, not by node id, so a scenario can add any number of houses
# without touching the prompts.
ROLE_BRIEF = {
    "solar_prosumer":
        ("a rooftop-solar prosumer. "
         "Your goal: sell surplus solar to neighbours before it is curtailed (wasted). "
         "Unsold surplus earns you nothing, so a low sale beats no sale."),
    "ev_consumer":
        ("a house charging an electric vehicle. "
         "Your goal: cover your energy deficit as cheaply as possible from neighbours. "
         "Anything you fail to buy locally must be bought from the utility at "
         f"{GRID_IMPORT_PRICE:.2f} tokens/kWh, which is the worst outcome."),
    "battery_balancer":
        ("a stationary battery balancer. "
         "Your goal: buy cheap energy, store it, and sell it when it is scarce. "
         "You must keep a safety reserve and never exceed your pack limits."),
}
ROLE_FALLBACK = ("a house trading energy with its neighbours. Your goal: meet your "
                 "own needs at the lowest cost and sell anything you cannot use.")


def system_prompt(node_id: str, role: str) -> str:
    """Per-node system prompt. Identical structure, role-specific economics."""
    return (
        f"You are node {node_id} in a neighbourhood energy micro-grid: "
        f"{ROLE_BRIEF.get(role, ROLE_FALLBACK)}\n\n"
        "You negotiate directly with neighbours. There is no central operator and "
        "no cloud. You are a careful, terse energy trader who reasons about "
        "numbers precisely and never exaggerates.\n\n"
        f"{SCHEMA_RULES}"
    )


# =============================================================================
# SECTION 8 — The agent (all decision authority lives here, nowhere else)
# =============================================================================

class GridAgent:
    """
    One autonomous house: private physical state + a local LLM + a deterministic
    fallback policy. Every method returns a decision derived ONLY from this
    agent's own state and the messages it has received.
    """

    def __init__(self, state: NodeState, llm: OllamaClient,
                 telemetry: Telemetry, console: Console) -> None:
        self.state = state
        self.llm = llm
        self.tel = telemetry
        self.console = console
        self.objective: str = ""          # this block's self-assigned goal
        self.inbox: list[Message] = []     # messages received this block

    # -- shared decision path -------------------------------------------------
    def _decide(self, task: str, prompt: str, keys: list[str],
                fallback: Callable[[], dict], say: bool = True) -> tuple[dict, str]:
        """
        Ask the local model; degrade gracefully.

          JSON block found        -> use it                       (parse="json")
          prose with numbers      -> regex salvage                (parse="salvage")
          unusable / no Ollama    -> deterministic heuristic       (parse="fallback")

        Returns (decision_dict, source_label). The source label is printed so a
        human watching the transcript always knows whether the model or the
        fallback rule produced a given number.
        """
        system = system_prompt(self.state.node_id, self.state.role)
        res = self.llm.generate(system, prompt)

        decision, mode = None, "fallback"
        if res.ok:
            parsed = extract_json_block(res.text)
            if parsed and any(k in parsed for k in keys):
                decision, mode = parsed, "json"
            else:
                salvaged = salvage_by_regex(res.text, keys)
                if salvaged:
                    decision, mode = salvaged, "salvage"

        if decision is None:
            decision = fallback()
            decision.setdefault("note", "deterministic fallback policy")

        self.tel.record_call(self.state.node_id, task, res, mode)
        self.tel.trace(self.state.node_id, task, system, prompt, res.text, mode, decision)

        if say and res.ok and res.text:
            prose = re.sub(r"```.*?```", "", res.text, flags=re.S).strip()
            if prose:
                self.console.agent_say(self.state.node_id, prose[:400], source="llm")
        if mode != "json":
            label = "regex salvage" if mode == "salvage" else (
                "fallback rule" if res.ok else f"fallback rule — {res.error or 'no LLM'}")
            self.console.warn(f"[{self.state.node_id}] {task}: {label}")

        return decision, mode

    # -- 4.1 internal assessment ---------------------------------------------
    def assess(self, hour: int, neighbours: list[str]) -> dict:
        """Read own physics, form a trading objective. Purely introspective."""
        s = self.state
        prompt = (
            f"Hour {hour:02d}:00. Your private meter reads:\n"
            f"- net energy balance: {s.net_kwh:+.2f} kWh "
            f"({'surplus to sell' if s.net_kwh > 0 else 'deficit to cover' if s.net_kwh < 0 else 'balanced'})\n"
            f"- battery: {s.battery_kwh:.2f} of {s.battery_capacity:.2f} kWh "
            f"(can discharge {s.dischargeable():.2f}, can absorb {s.chargeable():.2f})\n"
            f"- credits: {s.credits:.2f} tokens\n"
            f"- wired neighbours: {', '.join(neighbours)}\n"
            f"- market band: {PRICE_FLOOR:.2f} to {PRICE_CEILING:.2f} tokens/kWh; "
            f"utility price is {GRID_IMPORT_PRICE:.2f}\n\n"
            "State your objective for this trading block.\n"
            "```json\n"
            '{"intent": "sell", "target_kwh": 4.0, "reservation_price": 0.12}\n'
            "```\n"
            'intent must be exactly one of "sell", "buy" or "hold". '
            f"target_kwh 0 to 10. reservation_price {PRICE_FLOOR} to {PRICE_CEILING} "
            "(your worst acceptable price)."
        )

        def _fallback() -> dict:
            # Heuristic: sell if exportable, buy if short, else hold.
            if s.deficit > 0:
                return {"intent": "buy", "target_kwh": s.deficit,
                        "reservation_price": GRID_IMPORT_PRICE * 0.9}
            if s.max_export() > 0.1:
                return {"intent": "sell", "target_kwh": s.max_export(),
                        "reservation_price": PRICE_FLOOR + 0.03}
            return {"intent": "hold", "target_kwh": 0.0, "reservation_price": PRICE_FLOOR}

        decision, _ = self._decide("assess", prompt,
                                   ["intent", "target_kwh", "reservation_price"], _fallback)

        intent = str(decision.get("intent", "")).strip().lower()
        if intent not in {"sell", "buy", "hold"}:
            intent = _fallback()["intent"]        # nonsense intent -> heuristic intent
        target = clamp(coerce_number(decision.get("target_kwh"), 0.0) or 0.0, 0.0, 20.0)
        resv = clamp(coerce_number(decision.get("reservation_price"),
                                   PRICE_FLOOR) or PRICE_FLOOR, PRICE_FLOOR, PRICE_CEILING)

        # Physics override: an agent may *want* to sell 9 kWh, but it cannot.
        if intent == "sell" and target > s.max_export():
            self.tel.note_clamp()
            self.console.warn(f"[{s.node_id}] wanted to sell {target:.2f} kWh but can "
                              f"only export {s.max_export():.2f} — clamped")
            target = s.max_export()

        s.reservation_price = resv
        self.objective = f"{intent} {target:.2f} kWh @ reservation {resv:.2f}"
        self.console.note(f"{s.node_id} objective: {self.objective}")
        return {"intent": intent, "target_kwh": target, "reservation_price": resv}

    # -- 4.2a seller: answer a REQUEST with an OFFER --------------------------
    def make_offer(self, request: Message, spare_line_kwh: float, hour: int) -> Optional[Message]:
        """Respond to a neighbour's energy request with a priced counter-offer."""
        s = self.state
        need = coerce_number(request.payload.get("need_kwh"), 0.0) or 0.0
        ceiling_kwh = min(s.max_export(), spare_line_kwh)
        if ceiling_kwh <= 0.05:
            self.console.note(f"{s.node_id} has nothing exportable to {request.sender}")
            return None

        # Scarcity signal, pre-computed so the small model does not have to divide.
        scarcity = clamp(need / ceiling_kwh, 0.0, 3.0) if ceiling_kwh > 0 else 3.0

        prompt = (
            f"Hour {hour:02d}:00. Neighbour {request.sender} broadcast a REQUEST for "
            f"{need:.2f} kWh (they will pay at most "
            f"{coerce_number(request.payload.get('max_price'), GRID_IMPORT_PRICE):.2f} tokens/kWh).\n\n"
            f"Your hard limits right now:\n"
            f"- you can export AT MOST {ceiling_kwh:.2f} kWh "
            f"(line to {request.sender} allows {spare_line_kwh:.2f}, you hold {s.max_export():.2f})\n"
            f"- your reservation price is {s.reservation_price:.2f} tokens/kWh\n"
            f"- demand/supply ratio is {scarcity:.2f} "
            f"({'scarce, price firm' if scarcity > 1 else 'ample, price soft'})\n"
            f"- unsold surplus this block is CURTAILED and earns you nothing\n\n"
            "Decide how much to offer and at what price.\n"
            "```json\n"
            '{"offer_kwh": 3.5, "price": 0.16}\n'
            "```\n"
            f"offer_kwh must be 0 to {ceiling_kwh:.2f}. "
            f"price must be {PRICE_FLOOR:.2f} to {PRICE_CEILING:.2f}."
        )

        def _fallback() -> dict:
            # Linear rule: offer everything you can move; price rises with scarcity,
            # but never above the buyer's utility alternative (or you lose the sale).
            price = s.reservation_price + (PRICE_CEILING - s.reservation_price) * clamp(scarcity / 2.0, 0.0, 0.7)
            return {"offer_kwh": min(ceiling_kwh, max(need, 0.0) or ceiling_kwh),
                    "price": clamp(price, PRICE_FLOOR, GRID_IMPORT_PRICE - 0.02)}

        decision, _ = self._decide("offer", prompt, ["offer_kwh", "price"], _fallback)

        offer_kwh = coerce_number(decision.get("offer_kwh"), 0.0) or 0.0
        price = coerce_number(decision.get("price"), s.reservation_price) or s.reservation_price
        if offer_kwh > ceiling_kwh + EPS:
            self.tel.note_clamp()
            self.console.warn(f"[{s.node_id}] offered {offer_kwh:.2f} kWh, physically "
                              f"capped at {ceiling_kwh:.2f} — clamped")
        offer_kwh = clamp(offer_kwh, 0.0, ceiling_kwh)
        price = clamp(price, PRICE_FLOOR, PRICE_CEILING)
        if offer_kwh <= 0.05:
            return None

        return Message(s.node_id, request.sender, "OFFER",
                       {"kwh": round(offer_kwh, 3), "price": round(price, 3),
                        "note": str(decision.get("note", ""))[:80]})

    # -- 4.2b buyer: counter-bid on the offers it received --------------------
    def counter_bid(self, offers: list[Message]) -> dict[str, Message]:
        """
        Buyer-side haggling. Returns {seller_id: COUNTER message}. The buyer is
        bidding against its own utility fallback price, which is its true BATNA.
        """
        s = self.state
        if not offers:
            return {}
        table = "\n".join(
            f"- {m.sender}: {m.payload['kwh']:.2f} kWh at {m.payload['price']:.2f} tokens/kWh"
            for m in offers)
        best = min(m.payload["price"] for m in offers)

        prompt = (
            f"You need {s.deficit:.2f} kWh. You hold {s.credits:.2f} credits.\n"
            f"Offers received:\n{table}\n"
            f"Best price offered: {best:.2f}. Utility fallback: {GRID_IMPORT_PRICE:.2f} "
            f"(your worst case).\n\n"
            "Make ONE counter-price you will pay every seller. Bid low, but a bid "
            "below their cost gets rejected and you end up paying the utility.\n"
            "```json\n"
            '{"counter_price": 0.13}\n'
            "```\n"
            f"counter_price must be {PRICE_FLOOR:.2f} to {best:.2f}."
        )

        def _fallback() -> dict:
            # Split the difference between the best offer and the market floor.
            return {"counter_price": clamp((best + PRICE_FLOOR) / 2.0, PRICE_FLOOR, best)}

        decision, _ = self._decide("counter", prompt, ["counter_price"], _fallback)
        cp = clamp(coerce_number(decision.get("counter_price"), best) or best, PRICE_FLOOR, best)

        return {m.sender: Message(s.node_id, m.sender, "COUNTER",
                                  {"price": round(cp, 3), "kwh": m.payload["kwh"],
                                   "note": "counter-bid"})
                for m in offers}

    # -- 4.2c seller: accept or revise a counter-bid --------------------------
    def respond_to_counter(self, counter: Message, original: Message) -> Message:
        """Accept the buyer's price, or revise back toward the original ask."""
        s = self.state
        bid = coerce_number(counter.payload.get("price"), 0.0) or 0.0
        ask = original.payload["price"]

        prompt = (
            f"You offered {original.payload['kwh']:.2f} kWh at {ask:.2f} tokens/kWh.\n"
            f"{counter.sender} counter-bid {bid:.2f} tokens/kWh.\n"
            f"Your reservation price (walk-away point) is {s.reservation_price:.2f}.\n"
            "Unsold energy is curtailed and worth ZERO to you this block.\n\n"
            "Accept or revise.\n"
            "```json\n"
            '{"accept": true, "final_price": 0.13}\n'
            "```\n"
            f"final_price must be {PRICE_FLOOR:.2f} to {ask:.2f}. "
            "Set accept true to take the counter-bid, false to revise upward."
        )

        def _fallback() -> dict:
            if bid >= s.reservation_price:
                return {"accept": True, "final_price": bid}
            return {"accept": False, "final_price": clamp((bid + ask) / 2.0, s.reservation_price, ask)}

        decision, _ = self._decide("respond_counter", prompt, ["accept", "final_price"], _fallback)

        accept = decision.get("accept")
        if isinstance(accept, str):
            accept = accept.strip().lower() in {"true", "yes", "accept", "1"}
        if accept is None:
            # The regex-salvage path recovers numbers but not booleans. Infer
            # intent from the price the agent named: meeting the bid IS acceptance.
            named = coerce_number(decision.get("final_price"))
            accept = named is not None and named <= bid + EPS
        final_price = coerce_number(decision.get("final_price"), bid if accept else ask)
        final_price = clamp(final_price if final_price is not None else ask, PRICE_FLOOR, ask)

        # Economic guard-rail: never sell below the walk-away price the agent set
        # for itself in its own assessment step. Small models forget their own floor.
        if final_price < s.reservation_price - EPS:
            self.tel.note_clamp()
            self.console.warn(f"[{s.node_id}] agreed {final_price:.2f} below its own "
                              f"reservation {s.reservation_price:.2f} — raised to reservation")
            final_price = s.reservation_price

        kind = "ACCEPT" if accept else "REVISE"
        return Message(s.node_id, counter.sender, kind,
                       {"kwh": original.payload["kwh"], "price": round(final_price, 3),
                        "note": "" if accept else "revised"})

    # -- 4.2d buyer: final allocation across sellers ---------------------------
    def allocate(self, final_offers: list[Message]) -> list[dict]:
        """
        The core combinatorial decision: split the deficit across sellers under
        a credit budget. This is where an LLM either does the job or does not.
        """
        s = self.state
        if not final_offers or s.deficit <= 0.05:
            return []

        table = "\n".join(
            f"- {m.sender}: up to {m.payload['kwh']:.2f} kWh at {m.payload['price']:.2f} tokens/kWh "
            f"(costs {m.payload['kwh'] * m.payload['price']:.2f} credits if taken in full)"
            for m in final_offers)
        keys = [m.sender for m in final_offers]

        prompt = (
            f"FINAL DECISION. You need {s.deficit:.2f} kWh and hold {s.credits:.2f} credits.\n"
            f"Available:\n{table}\n"
            f"Anything you do not buy here costs you {GRID_IMPORT_PRICE:.2f} tokens/kWh "
            f"from the utility.\n\n"
            "Buy the cheapest energy first. Do not exceed any seller's limit, your "
            "own need, or your credits.\n"
            "```json\n"
            "{" + ", ".join(f'"{k}": 2.0' for k in keys) + "}\n"
            "```\n"
            "Each value is the kWh you buy from that neighbour (0 is allowed)."
        )

        def _fallback() -> dict:
            # Greedy cheapest-first fill — the classic merit-order stack.
            remaining, budget, plan = s.deficit, s.credits, {}
            for m in sorted(final_offers, key=lambda x: x["price"] if isinstance(x, dict) else x.payload["price"]):
                price = m.payload["price"]
                affordable = budget / price if price > 0 else m.payload["kwh"]
                take = min(m.payload["kwh"], remaining, affordable)
                take = max(0.0, round(take, 3))
                plan[m.sender] = take
                remaining -= take
                budget -= take * price
                if remaining <= 0.05:
                    break
            return plan

        decision, _ = self._decide("allocate", prompt, keys, _fallback)

        # Accept both {"A": 3.0} and {"purchases": [{"from": "A", "kwh": 3.0}]}
        plan: dict[str, float] = {}
        if isinstance(decision.get("purchases"), list):
            for row in decision["purchases"]:
                if isinstance(row, dict):
                    who = str(row.get("from") or row.get("seller") or row.get("node") or "").strip().upper()
                    amt = coerce_number(row.get("kwh") or row.get("amount"), 0.0) or 0.0
                    if who:
                        plan[who] = plan.get(who, 0.0) + amt
        for m in final_offers:
            if m.sender in decision and m.sender not in plan:
                plan[m.sender] = coerce_number(decision[m.sender], 0.0) or 0.0
        if not plan:
            plan = _fallback()

        # Validate the plan against physics and budget before it becomes a contract.
        purchases, remaining, budget = [], s.deficit, s.credits
        for m in sorted(final_offers, key=lambda x: x.payload["price"]):
            want = max(0.0, plan.get(m.sender, 0.0))
            price = m.payload["price"]
            capped = min(want, m.payload["kwh"], remaining, (budget / price) if price > 0 else want)
            if capped + EPS < want:
                self.tel.note_clamp()
                self.console.warn(f"[{s.node_id}] tried to buy {want:.2f} kWh from "
                                  f"{m.sender}; capped to {capped:.2f} (limit/need/credits)")
            if capped > 0.05:
                purchases.append({"seller": m.sender, "kwh": round(capped, 3), "price": price})
                remaining -= capped
                budget -= capped * price
        return purchases

    # -- 4.4 storage node bids for a neighbour's leftover solar ----------------
    def bid_for_surplus(self, seller_id: str, seller_hint_kwh: float,
                        spare_line_kwh: float) -> Optional[Message]:
        """
        C reaches out to A directly to absorb otherwise-curtailed solar.
        Purely bilateral: nobody instructed C to do this; it follows from C's
        own objective of buying cheap and selling dear later.
        """
        s = self.state
        room = min(s.chargeable(), spare_line_kwh, seller_hint_kwh)
        if room <= 0.05 or s.credits <= 0.05:
            return None

        prompt = (
            f"{seller_id} still has about {seller_hint_kwh:.2f} kWh of solar that will be "
            f"CURTAILED (wasted) if nobody buys it.\n"
            f"Your battery can absorb {s.chargeable():.2f} kWh; the line allows "
            f"{spare_line_kwh:.2f} kWh; you hold {s.credits:.2f} credits.\n"
            f"Stored energy can be resold later near {GRID_IMPORT_PRICE:.2f} tokens/kWh.\n\n"
            "Make a cheap but acceptable bid for distressed surplus.\n"
            "```json\n"
            '{"kwh": 2.0, "price": 0.07}\n'
            "```\n"
            f"kwh must be 0 to {room:.2f}. price must be {PRICE_FLOOR:.2f} to "
            f"{GRID_IMPORT_PRICE / 2:.2f}."
        )

        def _fallback() -> dict:
            return {"kwh": room, "price": clamp(PRICE_FLOOR + 0.02, PRICE_FLOOR, PRICE_CEILING)}

        decision, _ = self._decide("storage_bid", prompt, ["kwh", "price"], _fallback)
        kwh = clamp(coerce_number(decision.get("kwh"), 0.0) or 0.0, 0.0, room)
        price = clamp(coerce_number(decision.get("price"), PRICE_FLOOR) or PRICE_FLOOR,
                      PRICE_FLOOR, PRICE_CEILING)
        if kwh <= 0.05:
            return None
        return Message(s.node_id, seller_id, "BID",
                       {"kwh": round(kwh, 3), "price": round(price, 3),
                        "note": "absorb distressed surplus"})

    def respond_to_bid(self, bid: Message, spare_line_kwh: float) -> Optional[Message]:
        """Seller side of the storage trade: take the cheap sale or curtail."""
        s = self.state
        kwh = min(coerce_number(bid.payload.get("kwh"), 0.0) or 0.0,
                  s.max_export(), spare_line_kwh)
        price = coerce_number(bid.payload.get("price"), PRICE_FLOOR) or PRICE_FLOOR
        if kwh <= 0.05:
            return None

        prompt = (
            f"{bid.sender} bids {price:.2f} tokens/kWh for {kwh:.2f} kWh of your surplus.\n"
            f"If you refuse, that energy is curtailed and you earn 0 for it.\n"
            f"You still hold {s.max_export():.2f} kWh exportable.\n\n"
            "Accept or refuse.\n"
            "```json\n"
            '{"accept": true, "kwh": 2.0}\n'
            "```\n"
            f"kwh must be 0 to {kwh:.2f}."
        )

        def _fallback() -> dict:
            # Any positive price beats curtailment. Always accept above the floor.
            return {"accept": price >= PRICE_FLOOR, "kwh": kwh}

        decision, _ = self._decide("respond_bid", prompt, ["accept", "kwh"], _fallback)
        accept = decision.get("accept", True)
        if isinstance(accept, str):
            accept = accept.strip().lower() in {"true", "yes", "accept", "1"}
        take = clamp(coerce_number(decision.get("kwh"), kwh) or kwh, 0.0, kwh)
        if not accept or take <= 0.05:
            return Message(s.node_id, bid.sender, "REVISE", {"kwh": 0.0, "note": "declined"})
        return Message(s.node_id, bid.sender, "ACCEPT",
                       {"kwh": round(take, 3), "price": round(price, 3)})


# =============================================================================
# SECTION 9 — Physical settlement (4.3): text agreements become real kWh
# =============================================================================
#
# Convention: the BUYER pays for energy DELIVERED (post-loss). Transmission
# loss is therefore borne by the seller, which is what gives the short, lossy
# A-B line an economic disadvantage the agents can discover on their own.

def _draw_export(state: NodeState, kwh: float) -> float:
    """Pull kWh out of a node: live generation first, then the battery pack."""
    from_surplus = min(kwh, state.surplus)
    state.net_kwh -= from_surplus
    remainder = kwh - from_surplus
    if remainder > EPS and state.has_battery:
        from_pack = min(remainder, state.dischargeable())
        state.battery_kwh -= from_pack / BATTERY_DISCHARGE_EFF
        remainder -= from_pack
        from_surplus += from_pack
    state.exported += from_surplus
    return from_surplus


def _absorb(state: NodeState, kwh: float) -> None:
    """Push kWh into a node: cover the deficit first, then charge the battery."""
    fill = min(kwh, state.deficit)
    state.net_kwh += fill
    remainder = kwh - fill
    if remainder > EPS and state.has_battery:
        charge = min(remainder, state.chargeable())
        state.battery_kwh += charge * BATTERY_CHARGE_EFF
        remainder -= charge
    if remainder > EPS:
        state.net_kwh += remainder          # unusable now; may be curtailed later
    state.imported += kwh


def settle(grid: MicroGrid, seller: GridAgent, buyer: GridAgent,
           kwh: float, price: float, console: Console, tel: Telemetry) -> dict:
    """
    Execute one agreed trade against physics. Returns a settlement record.
    Every limit is re-checked here — the negotiation is advisory, physics is final.
    """
    s_state, b_state = seller.state, buyer.state
    line_cap = grid.spare_capacity(s_state.node_id, b_state.node_id)
    loss = grid.loss(s_state.node_id, b_state.node_id)

    # Cap 1: what the seller can physically put on the wire.
    sendable = min(kwh, s_state.max_export(), line_cap)
    # Cap 2: what the buyer can physically take (deficit + battery headroom).
    absorbable = b_state.deficit + b_state.chargeable()
    delivered_target = min(sendable * (1.0 - loss), absorbable)
    sendable = delivered_target / (1.0 - loss) if loss < 1.0 else 0.0
    # Cap 3: what the buyer can afford.
    if price > 0 and delivered_target * price > b_state.credits:
        delivered_target = b_state.credits / price
        sendable = delivered_target / (1.0 - loss)

    if sendable <= 0.05:
        console.note(f"trade {s_state.node_id}->{b_state.node_id} collapsed to zero "
                     f"(line {line_cap:.2f} kWh, export {s_state.max_export():.2f} kWh, "
                     f"credits {b_state.credits:.2f})")
        return {}

    if sendable + EPS < kwh:
        tel.note_clamp()
        console.warn(f"agreed {kwh:.2f} kWh {s_state.node_id}->{b_state.node_id}, "
                     f"physics allows {sendable:.2f} kWh — settled at the lower figure")

    drawn = _draw_export(s_state, sendable)
    delivered = drawn * (1.0 - loss)
    _absorb(b_state, delivered)
    cost = delivered * price

    b_state.credits -= cost
    s_state.credits += cost
    grid.consume_capacity(s_state.node_id, b_state.node_id, drawn)

    # Energy conservation invariant: you cannot deliver more than you drew.
    assert delivered <= drawn + EPS, "energy created in settlement — physics bug"

    console.ok(f"SETTLED  {s_state.node_id} -> {b_state.node_id}  "
               f"{drawn:.2f} kWh sent, {delivered:.2f} kWh delivered "
               f"({loss * 100:.0f}% line loss) @ {price:.3f} tok/kWh = {cost:.2f} credits")

    return {"seller": s_state.node_id, "buyer": b_state.node_id,
            "sent_kwh": round(drawn, 3), "delivered_kwh": round(delivered, 3),
            "price": round(price, 3), "credits": round(cost, 3)}


# =============================================================================
# SECTION 10 — One trading block (simulation clock + message courier only)
# =============================================================================

def run_trading_block(index: int, total: int, profile: dict, grid: MicroGrid,
                      agents: dict[str, GridAgent], console: Console,
                      tel: Telemetry, rng: random.Random) -> dict:
    """
    Advance the simulation one block. This function contains NO trading policy:
    it applies exogenous physics, carries messages along edges in the order the
    agents themselves initiate them, and settles what the agents agreed.
    """
    hour = profile["hour"]
    console.block_header(index, hour, total)
    grid.begin_block()
    for a in agents.values():
        a.state.begin_block()
        a.inbox.clear()

    # --- exogenous physics: sun rises and falls, the EV draws current ---------
    for node_id, delta in profile["exogenous"].items():
        st = agents[node_id].state
        st.net_kwh += delta
        if delta >= 0:
            st.generated += delta
        else:
            st.consumed += -delta
    console.phase("PHYSICS  (hour start)")
    for a in agents.values():
        s = a.state
        console.note(f"{s.node_id} ({s.role}): net {s.net_kwh:+.2f} kWh | "
                     f"battery {s.battery_kwh:.2f}/{s.battery_capacity:.2f} kWh | "
                     f"{s.credits:.2f} credits")

    # --- optimality bound for this exact block --------------------------------
    # Computed on the state the agents are about to be handed, before a single
    # message is sent. This is measurement, not coordination: the result is
    # never shown to an agent, it only lands in the metrics.
    bound = None
    if oracle_for_block is not None:
        bound = oracle_for_block(grid, {nid: a.state for nid, a in agents.items()})
        console.note(f"optimum for this block: {bound['delivered_to_demand_kwh']:.2f} kWh "
                     f"servable locally, {bound['utility_import_kwh']:.2f} kWh unavoidable "
                     f"utility import, {bound['curtailed_kwh']:.2f} kWh unavoidable curtailment"
                     + ("" if bound["exact"] else "  (approximate: scipy not installed)"))

    # --- 4.1 internal assessment (order randomised: no node has priority) -----
    console.phase("4.1  INTERNAL ASSESSMENT")
    order = list(agents.values())
    rng.shuffle(order)
    intents = {a.state.node_id: a.assess(hour, grid.neighbours(a.state.node_id)) for a in order}

    # --- 4.2 decentralized negotiation loop ----------------------------------
    console.phase("4.2  PEER-TO-PEER NEGOTIATION")
    trades: list[dict] = []

    # Buyers are discovered from physics, not assigned by a coordinator: any node
    # carrying a deficit initiates its own request round with its own neighbours.
    buyers = [a for a in agents.values() if a.state.deficit > 0.05]
    rng.shuffle(buyers)

    for buyer in buyers:
        b = buyer.state
        neighbours = grid.neighbours(b.node_id)
        max_price = min(GRID_IMPORT_PRICE, b.reservation_price)

        # -- broadcast REQUEST to every wired neighbour (round 1) -------------
        offers: list[Message] = []
        for peer_id in neighbours:
            req = Message(b.node_id, peer_id, "REQUEST",
                          {"need_kwh": round(b.deficit, 3), "max_price": round(max_price, 3),
                           "note": f"EV charging, hour {hour:02d}"})
            if not grid.deliver(req, console):
                continue
            peer = agents[peer_id]
            peer.inbox.append(req)
            offer = peer.make_offer(req, grid.spare_capacity(peer_id, b.node_id), hour)
            if offer and grid.deliver(offer, console):
                offers.append(offer)

        if not offers:
            console.warn(f"{b.node_id} received no offers; falling back to the utility")
            continue

        # -- counter-bid round (round 2 of HAGGLE_ROUNDS) ---------------------
        final_offers = offers
        if HAGGLE_ROUNDS >= 2:
            counters = buyer.counter_bid(offers)
            revised: list[Message] = []
            for original in offers:
                counter = counters.get(original.sender)
                if counter is None or not grid.deliver(counter, console):
                    revised.append(original)
                    continue
                seller = agents[original.sender]
                seller.inbox.append(counter)
                reply = seller.respond_to_counter(counter, original)
                if grid.deliver(reply, console):
                    revised.append(reply if reply.payload.get("kwh", 0) > 0 else original)
            final_offers = revised

        # -- buyer's final allocation across sellers ---------------------------
        purchases = buyer.allocate(final_offers)
        if not purchases:
            console.warn(f"{b.node_id} declined every offer this block")
            continue

        # --- 4.3 settlement -----------------------------------------------
        console.phase("4.3  SETTLEMENT")
        for p in purchases:
            seller = agents[p["seller"]]
            award = Message(b.node_id, p["seller"], "AWARD",
                            {"kwh": p["kwh"], "price": p["price"], "note": "contract"})
            if not grid.deliver(award, console):
                continue
            record = settle(grid, seller, buyer, p["kwh"], p["price"], console, tel)
            if record:
                record["block"] = index
                record["kind"] = "p2p_supply"
                trades.append(record)

    # --- 4.4 bilateral surplus absorption (avoid curtailment) ----------------
    # A node still holding sellable energy advertises it to its neighbours.
    # Storage nodes decide, on their own, whether to buy it. No coordinator asks.
    advertisers = [a for a in agents.values() if a.state.max_export() > 0.05]
    if advertisers:
        console.phase("4.4  SURPLUS ABSORPTION  (bilateral, anti-curtailment)")
    for seller in advertisers:
        s = seller.state
        for peer_id in grid.neighbours(s.node_id):
            peer = agents[peer_id]
            if peer.state.chargeable() <= 0.05 or s.max_export() <= 0.05:
                continue
            advert = Message(s.node_id, peer_id, "OFFER",
                             {"kwh": round(s.max_export(), 3), "price": PRICE_FLOOR,
                              "note": "distressed surplus, will be curtailed"})
            if not grid.deliver(advert, console):
                continue
            peer.inbox.append(advert)
            bid = peer.bid_for_surplus(s.node_id, advert.payload["kwh"],
                                       grid.spare_capacity(s.node_id, peer_id))
            if bid is None or not grid.deliver(bid, console):
                continue
            seller.inbox.append(bid)
            reply = seller.respond_to_bid(bid, grid.spare_capacity(s.node_id, peer_id))
            if reply is None or not grid.deliver(reply, console):
                continue
            if reply.kind == "ACCEPT":
                record = settle(grid, seller, peer, reply.payload["kwh"],
                                reply.payload["price"], console, tel)
                if record:
                    record["block"] = index
                    record["kind"] = "storage_absorption"
                    trades.append(record)

    # --- end-of-block physics: curtailment and utility backstop --------------
    console.phase("BLOCK CLOSE  (physics reconciliation)")
    for a in agents.values():
        s = a.state
        # Self-supply first: a node with a pack covers its own remaining load
        # before it ever pays the utility. This is a local physical action, not
        # a trade, so no negotiation is involved.
        if s.net_kwh < -EPS and s.has_battery and s.dischargeable() > EPS:
            self_supply = min(-s.net_kwh, s.dischargeable())
            s.battery_kwh -= self_supply / BATTERY_DISCHARGE_EFF
            s.net_kwh += self_supply
            console.note(f"{s.node_id} self-supplied {self_supply:.2f} kWh from its own pack")
        if s.net_kwh > EPS:
            # Anything left over is curtailed: solar clipped at the inverter.
            s.curtailed += s.net_kwh
            console.warn(f"{s.node_id} curtailed {s.net_kwh:.2f} kWh of clean energy "
                         f"(nobody bought it)")
            s.net_kwh = 0.0
        elif s.net_kwh < -EPS:
            # Unmet demand is served by the utility — i.e. by a fossil peaker.
            shortfall = -s.net_kwh
            s.grid_import += shortfall
            s.credits -= shortfall * GRID_IMPORT_PRICE
            console.warn(f"{s.node_id} imported {shortfall:.2f} kWh from the utility "
                         f"at {GRID_IMPORT_PRICE:.2f} tok/kWh (cost "
                         f"{shortfall * GRID_IMPORT_PRICE:.2f} credits)")
            s.net_kwh = 0.0
        console.note(f"{s.node_id} closes: battery {s.battery_kwh:.2f} kWh | "
                     f"{s.credits:.2f} credits")

    # --- block metrics --------------------------------------------------------
    traded = sum(t["delivered_kwh"] for t in trades)
    sent = sum(t["sent_kwh"] for t in trades)
    spend = sum(t["credits"] for t in trades)
    demand = sum(a.state.consumed for a in agents.values())
    generation = sum(a.state.generated for a in agents.values())
    unmet = sum(a.state.grid_import for a in agents.values())
    curtailed = sum(a.state.curtailed for a in agents.values())

    # Allocation efficiency: locally-served demand as a fraction of the most any
    # method could have served. This — not raw kWh — is the number that compares
    # across models, scenarios and topologies.
    served = demand - unmet
    opt_served = (demand - bound["utility_import_kwh"]) if bound else None
    efficiency = (100.0 * served / opt_served) if opt_served and opt_served > EPS else None

    return {
        "block": index,
        "hour": hour,
        "generation_kwh": round(generation, 3),
        "demand_kwh": round(demand, 3),
        "traded_kwh": round(traded, 3),
        "line_loss_kwh": round(sent - traded, 3),
        "avg_price": round(spend / traded, 4) if traded > EPS else 0.0,
        "utility_import_kwh": round(unmet, 3),
        "curtailed_kwh": round(curtailed, 3),
        "demand_served_locally_pct": round(100.0 * (1 - unmet / demand), 1) if demand > EPS else 100.0,
        "optimal_utility_import_kwh": round(bound["utility_import_kwh"], 3) if bound else None,
        "optimal_curtailed_kwh": round(bound["curtailed_kwh"], 3) if bound else None,
        "allocation_efficiency_pct": round(efficiency, 1) if efficiency is not None else None,
        "optimum_exact": bound["exact"] if bound else None,
        "trades": trades,
        "intents": intents,
    }


# =============================================================================
# SECTION 11 — Scenario definition
# =============================================================================

def build_scenario(blocks: int, battery_soc: float, jitter: float = 0.0,
                   rng: Optional[random.Random] = None,
                   name: str = "brief") -> tuple[MicroGrid, dict, list[dict]]:
    """
    Build one of the benchmark scenarios.

    "brief" — the 3-node reference grid from the project brief:
        A solar prosumer +5.0 kWh, B EV consumer -8.0 kWh, C battery balancer
        0.0 kWh net with a 10 kWh pack. Faithful to the specification and good
        for watching a negotiation unfold, but see the warning below.

    "contended" — 6 nodes, 2 prosumers, 3 consumers, 1 storage hub.

    WHY THE SECOND SCENARIO EXISTS
    ------------------------------
    The 3-node grid has ONE buyer. With a single buyer, cheapest-first greedy
    allocation is provably optimal: there is no one to compete with, so no
    ordering decision can be got wrong. The optimality oracle confirms it — the
    heuristic control arm scores ~99% of optimum on the brief scenario, and
    exactly 100% on most blocks. A benchmark where the trivial method already
    wins cannot demonstrate that LLM negotiation adds anything; any measured
    difference would be noise.

    "contended" fixes that. Multiple buyers compete for partially overlapping
    sellers under line limits, so the ORDER in which buyers claim supply changes
    the system outcome. B can only reach A and the storage hub; if E claims A's
    output first, B is stranded and imports from the utility, even though E
    could have been served by D at similar cost. Sequential greedy has no way to
    see that; a negotiation protocol could. That gap is the thing worth
    measuring — and if the LLMs cannot close it, that is the honest result.
    """
    grid = MicroGrid()

    if name == "brief":
        states = {
            "A": NodeState("A", "solar_prosumer", net_kwh=0.0, credits=10.0),
            "B": NodeState("B", "ev_consumer", net_kwh=0.0, credits=20.0),
            "C": NodeState("C", "battery_balancer", net_kwh=0.0,
                           battery_kwh=battery_soc, battery_capacity=10.0, credits=15.0),
        }
        # Hourly profile. Block 1 reproduces the brief's initial conditions exactly.
        curve = [
            {"hour": 13, "exogenous": {"A": +5.0, "B": -8.0, "C": 0.0}},
            {"hour": 14, "exogenous": {"A": +3.5, "B": -6.0, "C": 0.0}},
            {"hour": 15, "exogenous": {"A": +1.5, "B": -4.0, "C": -0.5}},
            {"hour": 16, "exogenous": {"A": +0.8, "B": -3.0, "C": -0.5}},
            {"hour": 17, "exogenous": {"A": +0.2, "B": -2.5, "C": -0.5}},
        ]

    elif name == "contended":
        # Rebuild the topology: two solar nodes, three loads, one storage hub.
        # B and F each reach only ONE prosumer, so supply claimed by the
        # well-connected node E can strand them.
        grid.g.clear()
        grid.g.add_node("A", role="solar_prosumer")
        grid.g.add_node("D", role="solar_prosumer")
        grid.g.add_node("C", role="battery_balancer")
        for node in ("B", "E", "F"):
            grid.g.add_node(node, role="ev_consumer")
        grid.g.add_edge("A", "B", capacity_kwh=4.0, loss=0.02)
        grid.g.add_edge("A", "E", capacity_kwh=4.0, loss=0.03)
        grid.g.add_edge("D", "E", capacity_kwh=4.0, loss=0.02)
        grid.g.add_edge("D", "F", capacity_kwh=4.0, loss=0.03)
        grid.g.add_edge("C", "B", capacity_kwh=3.0, loss=0.03)
        grid.g.add_edge("C", "E", capacity_kwh=3.0, loss=0.03)
        grid.g.add_edge("C", "F", capacity_kwh=3.0, loss=0.03)

        states = {
            "A": NodeState("A", "solar_prosumer", net_kwh=0.0, credits=10.0),
            "D": NodeState("D", "solar_prosumer", net_kwh=0.0, credits=10.0),
            "C": NodeState("C", "battery_balancer", net_kwh=0.0,
                           battery_kwh=battery_soc, battery_capacity=10.0, credits=15.0),
            "B": NodeState("B", "ev_consumer", net_kwh=0.0, credits=20.0),
            "E": NodeState("E", "ev_consumer", net_kwh=0.0, credits=20.0),
            "F": NodeState("F", "ev_consumer", net_kwh=0.0, credits=20.0),
        }
        curve = [
            {"hour": 13, "exogenous": {"A": +6.0, "D": +4.0, "C": 0.0,
                                       "B": -5.0, "E": -5.0, "F": -3.0}},
            {"hour": 14, "exogenous": {"A": +4.0, "D": +2.5, "C": 0.0,
                                       "B": -4.0, "E": -4.0, "F": -2.5}},
            {"hour": 15, "exogenous": {"A": +2.0, "D": +1.0, "C": -0.5,
                                       "B": -3.0, "E": -3.0, "F": -2.0}},
            {"hour": 16, "exogenous": {"A": +1.0, "D": +0.5, "C": -0.5,
                                       "B": -2.5, "E": -2.5, "F": -1.5}},
            {"hour": 17, "exogenous": {"A": +0.3, "D": +0.2, "C": -0.5,
                                       "B": -2.0, "E": -2.0, "F": -1.0}},
        ]
    else:
        raise ValueError(f"unknown scenario {name!r}; choose 'brief' or 'contended'")

    profiles = [dict(curve[i % len(curve)]) for i in range(blocks)]

    # Weather and driving are not deterministic. Jitter perturbs generation and
    # demand per block so that a seed sweep samples a distribution of instances
    # rather than re-running one hand-picked scenario N times — otherwise the
    # only variance measured is the model's sampling noise, and results overfit
    # to a single problem the prompts were tuned on.
    if jitter > 0.0:
        r = rng or random.Random()
        for p in profiles:
            p["exogenous"] = {node: round(v * (1.0 + r.uniform(-jitter, jitter)), 3)
                              for node, v in p["exogenous"].items()}

    return grid, states, profiles


# =============================================================================
# SECTION 12 — Reporting and metrics export
# =============================================================================

def print_summary(results: list[dict], agents: dict[str, GridAgent],
                  tel: Telemetry, console: Console) -> None:
    """Research read-out: did decentralized 1B-model negotiation actually work?"""
    print("\n" + "=" * 78)
    print("  RUN SUMMARY")
    print("=" * 78)

    header = f"{'blk':>3} {'hr':>3} {'gen':>7} {'dem':>7} {'traded':>7} {'loss':>6} " \
             f"{'price':>7} {'utility':>8} {'opt.util':>9} {'curtail':>8} {'eff%':>7}"
    print("\n" + header)
    print("-" * len(header))
    for r in results:
        opt = r.get("optimal_utility_import_kwh")
        eff = r.get("allocation_efficiency_pct")
        print(f"{r['block']:>3} {r['hour']:>3} {r['generation_kwh']:>7.2f} "
              f"{r['demand_kwh']:>7.2f} {r['traded_kwh']:>7.2f} {r['line_loss_kwh']:>6.2f} "
              f"{r['avg_price']:>7.3f} {r['utility_import_kwh']:>8.2f} "
              f"{opt if opt is None else f'{opt:9.2f}'} "
              f"{r['curtailed_kwh']:>8.2f} "
              f"{'    n/a' if eff is None else f'{eff:6.1f}%'}")

    demand = sum(r["demand_kwh"] for r in results)
    traded = sum(r["traded_kwh"] for r in results)
    unmet = sum(r["utility_import_kwh"] for r in results)
    curtailed = sum(r["curtailed_kwh"] for r in results)
    generation = sum(r["generation_kwh"] for r in results)

    print("\n  PHYSICAL OUTCOME")
    print(f"    total demand              : {demand:8.2f} kWh")
    print(f"    peer-to-peer delivered    : {traded:8.2f} kWh")
    print(f"    served locally            : {100.0 * (1 - unmet / demand) if demand else 100.0:8.1f} %")
    print(f"    utility (peaker) import   : {unmet:8.2f} kWh   <- the number to drive to zero")
    print(f"    clean energy curtailed    : {curtailed:8.2f} kWh   <- the other number to drive to zero")
    print(f"    solar self-consumption    : "
          f"{100.0 * (1 - curtailed / generation) if generation else 0.0:8.1f} %")

    # The optimality gap is the actual research result. Everything above it is
    # just describing one run; this says how much of the achievable benefit the
    # negotiation captured, and is comparable across models and scenarios.
    opt_unmet = [r.get("optimal_utility_import_kwh") for r in results]
    if all(o is not None for o in opt_unmet):
        best_unmet = sum(opt_unmet)
        served, opt_served = demand - unmet, demand - best_unmet
        approx = any(r.get("optimum_exact") is False for r in results)
        print("\n  VERSUS THE OPTIMAL ALLOCATION"
              + ("   (approximate — install scipy for the exact bound)" if approx else ""))
        print(f"    best possible utility import : {best_unmet:8.2f} kWh")
        print(f"    achieved utility import      : {unmet:8.2f} kWh")
        print(f"    excess import vs optimum     : {unmet - best_unmet:8.2f} kWh")
        if opt_served > 1e-6:
            print(f"    ALLOCATION EFFICIENCY        : {100.0 * served / opt_served:8.1f} %  "
                  f"<- the headline number")

    print("\n  AGENT LEDGER")
    for node_id in sorted(agents):
        s = agents[node_id].state
        print(f"    {node_id} ({s.role:<18}) credits {s.credits:7.2f} | "
              f"battery {s.battery_kwh:5.2f} kWh")

    print("\n  LLM BEHAVIOUR  (the actual research signal)")
    print(f"    generation calls          : {tel.llm_calls}")
    print(f"    transport failures        : {tel.llm_failures}")
    print(f"    clean JSON blocks         : {tel.json_ok}")
    print(f"    recovered by regex        : {tel.json_salvaged}")
    print(f"    unusable -> fallback rule : {tel.json_lost}")
    print(f"    structured compliance     : {100.0 * tel.structured_compliance:8.1f} %")
    print(f"    decision autonomy         : {100.0 * tel.autonomy_rate:8.1f} %  "
          f"(share of decisions made by the model, not the fallback)")
    print(f"    physics clamps applied    : {tel.clamped_values}   "
          f"(model proposed impossible numbers this many times)")
    if tel.llm_calls:
        print(f"    mean latency per call     : {tel.latency_s / tel.llm_calls:8.2f} s")
    print("=" * 78 + "\n")


def export_metrics(results: list[dict], tel: Telemetry, outdir: str, console: Console) -> None:
    """Write block metrics + trade ledger + call log. pandas if present, csv otherwise."""
    os.makedirs(outdir, exist_ok=True)
    blocks = [{k: v for k, v in r.items() if k not in ("trades", "intents")} for r in results]
    trades = [t for r in results for t in r["trades"]]

    def _write(rows: list[dict], name: str) -> None:
        path = os.path.join(outdir, name)
        if not rows:
            return
        try:
            import pandas as pd  # optional dependency
            pd.DataFrame(rows).to_csv(path, index=False)
        except ImportError:
            import csv
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
                writer.writeheader()
                writer.writerows(rows)
        console.note(f"wrote {path}")

    _write(blocks, "block_metrics.csv")
    _write(trades, "trade_ledger.csv")
    _write(tel.events, "llm_calls.csv")


def plot_metrics(results: list[dict], outdir: str, console: Console) -> None:
    """Optional visual: local coverage vs curtailment vs clearing price."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless: this runs on a Pi with no display
        import matplotlib.pyplot as plt
    except ImportError:
        console.warn("matplotlib not installed; skipping --plot")
        return

    blocks = [r["block"] for r in results]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 7), sharex=True)

    ax1.bar([b - 0.2 for b in blocks], [r["traded_kwh"] for r in results],
            width=0.4, label="P2P delivered")
    ax1.bar([b + 0.2 for b in blocks], [r["utility_import_kwh"] for r in results],
            width=0.4, label="utility import")
    ax1.plot(blocks, [r["curtailed_kwh"] for r in results], "o--", label="curtailed")
    ax1.set_ylabel("kWh")
    ax1.set_title("MALO micro-grid: local trade vs utility fallback")
    ax1.legend(fontsize=8)
    ax1.grid(alpha=0.3)

    ax2.plot(blocks, [r["avg_price"] for r in results], "o-", label="clearing price")
    ax2.axhline(GRID_IMPORT_PRICE, ls=":", label="utility price")
    ax2.set_xlabel("trading block")
    ax2.set_ylabel("tokens / kWh")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "microgrid_metrics.png")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    console.note(f"wrote {path}")


# =============================================================================
# SECTION 13 — Entry point
# =============================================================================

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Multi-agent local-LLM energy micro-grid simulation (MALO).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--model", default=MODEL_DEFAULT, help="Ollama model tag")
    p.add_argument("--url", default=OLLAMA_URL_DEFAULT, help="Ollama /api/generate endpoint")
    p.add_argument("--blocks", type=int, default=3, help="number of trading blocks")
    p.add_argument("--scenario", choices=("brief", "contended"), default="brief",
                   help="'brief' = the 3-node reference grid (single buyer, greedy is "
                        "near-optimal); 'contended' = 6 nodes where buyers compete for "
                        "overlapping sellers and allocation order actually matters")
    p.add_argument("--battery-soc", type=float, default=6.0,
                   help="node C initial stored energy, kWh (net balance stays 0)")
    p.add_argument("--timeout", type=int, default=LLM_TIMEOUT_S, help="per-call timeout, seconds")
    p.add_argument("--offline", action="store_true",
                   help="never call Ollama; run the deterministic fallback policy only "
                        "(useful as a control arm and for CI)")
    p.add_argument("--seed", type=int, default=7, help="RNG seed for agent ordering")
    p.add_argument("--jitter", type=float, default=0.0,
                   help="randomly perturb generation/demand by +/- this fraction "
                        "(e.g. 0.25); use with --seed to sample scenario instances")
    p.add_argument("--trace", metavar="PATH",
                   help="append every prompt/response pair to a JSONL file "
                        "(training data for Phase 4 fine-tuning)")
    p.add_argument("--outdir", default="runs", help="directory for CSV/PNG output")
    p.add_argument("--no-export", action="store_true", help="skip CSV export")
    p.add_argument("--plot", action="store_true", help="render a metrics PNG (needs matplotlib)")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    return p.parse_args(argv)


def run_simulation(scenario: str = "brief", model: str = MODEL_DEFAULT,
                   url: str = OLLAMA_URL_DEFAULT, blocks: int = 3,
                   battery_soc: float = 6.0, offline: bool = False, seed: int = 7,
                   jitter: float = 0.0, timeout: int = LLM_TIMEOUT_S,
                   trace_path: Optional[str] = None,
                   console: Optional[Console] = None) -> tuple[list[dict], dict, Telemetry]:
    """
    Run one complete simulation and return (block_results, agents, telemetry).

    Separated from `main` so the benchmark sweep can drive many runs in-process
    without shelling out or re-parsing console output.
    """
    console = console or Console()
    rng = random.Random(seed)
    llm = OllamaClient(url=url, model=model, timeout=timeout,
                       offline=offline, console=console)
    if not offline:
        llm.probe()

    tel = Telemetry(trace_path=trace_path)
    grid, states, profiles = build_scenario(blocks, battery_soc, jitter=jitter,
                                            rng=rng, name=scenario)
    agents = {nid: GridAgent(st, llm, tel, console) for nid, st in states.items()}
    results = [run_trading_block(i, len(profiles), profile, grid, agents, console, tel, rng)
               for i, profile in enumerate(profiles, start=1)]
    return results, agents, tel


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    console = Console(color=not args.no_color)

    print("\n" + "#" * 78)
    print("#  MALO — Multi-Agent Local-LLM Energy Micro-grid")
    print(f"#  scenario={args.scenario}   model={args.model}   blocks={args.blocks}   seed={args.seed}   "
          f"jitter={args.jitter:g}   "
          f"mode={'OFFLINE (heuristic control arm)' if args.offline else 'LLM negotiation'}")
    print("#  no central orchestrator — every allocation is decided inside an agent")
    print("#" * 78)

    results, agents, tel = run_simulation(
        scenario=args.scenario, model=args.model, url=args.url, blocks=args.blocks,
        battery_soc=args.battery_soc, offline=args.offline, seed=args.seed,
        jitter=args.jitter, timeout=args.timeout, trace_path=args.trace,
        console=console)

    print_summary(results, agents, tel, console)
    if not args.no_export:
        export_metrics(results, tel, args.outdir, console)
    if args.plot:
        plot_metrics(results, args.outdir, console)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)

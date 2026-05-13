# Changelog

All notable changes to Aurora are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-05-XX

The substrate-layer release. Aurora becomes a platform other code can plug into.

**Positioning:** v1.1 formalises Aurora's dual surfaces with equal billing — **Aurora Copilot** (the Web Studio for humans) and **Aurora Cortex** (the verification layer AI systems call via SDK / MCP / Decision Contracts / Research Kit). One engine, two surfaces, same glass-box principles. *Cloud LLMs guess. Aurora computes.*

### Added

#### Substrate layers (Aurora Cortex)

- **Aurora SDK** (`aurora_sdk/`) — pure-Python public API:
  - `aurora.run("data.csv", depth=...)` end-to-end runner
  - `Bundle` class with `save()`, `load()`, `verify()`, `sign()`
  - `Findings` chained-filter view (`.critical()`, `.by_method()`, `.where()`)
  - `ForecastView` with `.peak(horizon_hours=...)`
  - `SystemModelView` with entity / relationship / phase-space accessors
- **Aurora Bundle Format v1** (`.aurora.json`) — stable, versioned, signable analytical artifact
  - SHA-256 integrity hash over canonical JSON serialisation
  - Optional Ed25519 signature when `cryptography` is installed
  - Bundle includes dataset SHA-256, run identity, full method registry, assumptions, fabricated_count
- **Aurora MCP server** (`aurora_mcp/`) — Model Context Protocol server with 7 tools:
  - `aurora_analyze`, `aurora_load_bundle`, `aurora_findings`, `aurora_forecast`, `aurora_explain`, `aurora_intervene`, `aurora_simulate`
  - Path-allowlist enforced per call; 2 MB output budget; never raises across boundary
- **Decision Contracts** (`fantasyai/aurora/decision_contracts/`) — programmable predicates that fire actions:
  - Trigger expressions with operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`, `contains`, `regex`
  - Special aggregates: `findings.crit_count`, `confidence`, `forecast.peak`, etc.
  - Webhook action with SSRF guard (private / loopback / link-local rejected unless opted in)
  - Log + File actions, with file action sandboxed to `AURORA_CONTRACTS_OUTPUT`
  - Authorization / X-API-Key / Cookie redacted in audit records
  - Rate-limit per contract (`max_per_minute|hour|day`)
- **Research Kit** (`fantasyai/aurora/research_kit.py`) — publication-ready output:
  - `methods.md` (LaTeX-ready Markdown with inline citations)
  - `references.bib` (BibTeX, one entry per cited prior; built-in library + placeholder for unknown)
  - `replication.json` (deterministic re-run config + bundle hash)
  - `.zenodo.json` (Zenodo deposit metadata, DOI-mintable)
  - Combined-method tags (e.g., `ISO-FOREST + ROBUST-Z`) cite both contributing methods

#### Glass-box UX

- **Aurora Pulse** banner — bear-voiced single-line status read at the top of the stage, dynamically generated from current state
- **`0 fabricated` trust chip** — live signal that the Aurora contract held for this run (turns red and counts up if any finding lacks provenance)
- **Assumptions strip** — Aurora declares its methods' baseline assumptions up front; click `more ▾` for the full per-method list
- **Method hint tooltips** — every finding card's METHOD chip explains what the method does in plain English on hover (20+ methods covered)
- **Dataset Lens panel** — Structure + Columns merged into a single panel under the run banner (relocated from the left rail)

#### Navigator bear (state-aware narrator)

- 4 moods (`idle / thinking / happy / concerned`) drive bear glow + speech-bubble accent color
- Dynamic copy: `complete` state reads anomaly count + confidence; `running` copy advances as elapsed time grows
- `complete` mood is confidence-tiered (≥60% happy, <30% concerned)
- New states: `failed` (anchors at error banner with reason) and `intervene_mode` / `simulate_mode` (contextual guidance)

#### Visual rhythm

- Scroll-docked cube navigator — fixed top-right mini widget when main cube scrolls out of view; click any face to scroll back + rotate cube
- Cross-view selection in System Graph (`__sgV5State.selectedNodeId` shared across GRAPH / SPACETIME / PHASE SPACE)
- Confidence badge color-tiered (red <30%, amber 30-60%, green ≥60%)
- Inline help panel ("How to read this graph?") with plain-English explanations of all three views + modes

#### Micro-interactions

- Skeleton-loader utility class for state transitions
- Optimistic UI on INTERVENE — source node tints violet immediately before API response
- Number tween helper (`setTweenedNumber`) — smooth interpolation on confidence percentages
- Keyboard shortcuts overlay (`?` toggle, `1-6` cube faces, `g/s/p` graph views, `i/v/m` modes, `/` ask, `r` run, `Esc` close)
- ⌨ ? button in top bar for shortcut discoverability
- Compact / Comfortable density toggle (persisted in localStorage)

#### Accessibility (Backlog #6)

- Global `:focus-visible` ring (cyan, 2 px outline + 4 px halo) on every interactive element
- Skip-to-content link as first tab-stop
- ARIA labels on Aurora Pulse, Assumptions Strip, Dataset Lens, cube dock, Start tile compact strip
- `aria-expanded` synced for all collapsible toggles
- Cube faces keyboard-activatable (`role="button"`, `tabindex="0"`, Enter/Space)

### Changed

- **System Graph during analysis** — `body.app-phase-running` class set by `showTopBanner()` hides stale system-graph + peek + dataset-lens + cube-dock while a new run is in flight. Findings + diary + run-indicator remain visible (they progressively repopulate).
- **`fabricated_count` detection** — now positive-signal-only (`fabricated=true` flag or `method=llm_generated`). Old heuristic flagged findings with missing optional fields as fabricated; that was a false-positive bug.
- **Phase A1 confidence-gated template fallback** in `system_model/instantiator.py` — when a template scores <0.40 AND <0.30 of its slots map to columns, fall back to a generic column-name graph rather than mis-instantiating. Fixes factory_bearing wrongly using `industrial` template and patient_cohort wrongly using `enviro`.
- **Phase A2 fuzzy column matching** in `system_model/spacetime.py` — last-resort substring + token matching when an entity's `data_column` is None, so worldlines populate even with mismatched slot names.
- **Phase A1.5 dataset path plumbing** in `state_builder.py` — `_build_dataset` now explicitly picks `dataset_key` from whichever artifact has it (was short-circuiting on `meta` and missing `structure.dataset_key`). Unblocks worldline rendering.
- **Synthesis cache** in `synthesis/engine.py` — `synthesis_cache.v1.json` written on first complete run, read on subsequent polls. Stops the RAG↔template flip-flop that happened when synthesis was re-run on every state poll.
- **Color contract** documented at the top of the palette variables — every color has one job. Phase-space NOW marker migrated from crit-red to cyan (it's "current position" info, not severity). Attractor color cycle reordered so crit-red is never used for a regime label.
- **Decision Contracts module** placed at `fantasyai/aurora/decision_contracts/` rather than `contracts/` to avoid colliding with the existing schema-contract module.

### Fixed

- **Top Anomalies tile** reads from `math_results.top_anomalies` with schema translation (anomaly synthesis no longer shows zero counts when the tile renders empty)
- **Subprocess argparse error** on `--also-motif --also-orchestrator` flags
- **Daemon thread pattern** preventing orphan-blocked subprocess exit
- **Stratified time-preserving sampling** preserves extreme outliers (|z| ≥ 4)
- **Copilot bear clipping** at top of panel
- **System graph staying visible during a new analysis** — `body.app-phase-running` class hides stale UI while the new run is in flight
- **`19 FABRICATED` false positive** — old heuristic flagged any finding without ALL of `method` + `claim_id` as fabricated; new logic only flags positive signals (`fabricated=true` flag, `method=llm_generated`)
- **Bundle integrity hash** is stable across regenerations — `generated_at` is excluded from the hashed content

### Security

- Aurora Bundle integrity hash + signing
- MCP path-allowlist + output-budget cap + JSON-only error wrapping
- Decision Contracts SSRF guard (private / loopback / link-local IPs rejected by default)
- Decision Contracts auth-header redaction in audit logs
- Decision Contracts file-action sandbox + traversal blocking
- Decision Contracts rate-limit per contract

### Tests

- 320 tests passing (up from 218 baseline):
  - +28 `tests/test_aurora_sdk.py` (Bundle roundtrip, integrity, signing, findings filters, fabricated count)
  - +19 `tests/test_aurora_mcp.py` (tool schemas, path security, load/findings/forecast/explain)
  - +34 `tests/test_decision_contracts.py` (operators, field paths, SSRF guard, rate limiting, persistence, audit redaction)
  - +22 `tests/test_research_kit.py` (methods.md sections, references.bib, replication.json, .zenodo.json, write_research_kit)
  - 2 tests skipped (require `cryptography` for Ed25519 signing — opt-in)
- 16 backend modules import cleanly
- HTML structural sanity: 2/2 asides, 7/7 sections, 1/1 main, all IDs unique

### Known Issues

- Mobile / tablet responsiveness deferred to v1.2
- Decision Contract action types limited to webhook / log / file (Slack / Discord / PagerDuty / email coming in v1.2)
- Knowledge bank ships with seed set; full ~2 GB pack downloaded separately
- Some advanced methods (Gaussian process MCMC) may produce numerically close but not bit-identical results on different platforms

### Docs

- README.md rewritten around the "quantitative cortex" pitch + 4 substrate surfaces
- ARCHITECTURE.md — full system design including substrate layer
- ROADMAP.md — v1.1 / v1.2 / v2.0 / v2.0+ public timeline
- SECURITY.md — threat model + supported versions + reporting
- CONTRIBUTING.md — workflow + style + substrate-layer backward-compat policy
- docs/sdk.md, docs/mcp.md, docs/decision-contracts.md, docs/research-kit.md — substrate layer guides

## [1.0.0] — 2026-04-XX

Initial private alpha. Foundation work, demo datasets, basic synthesis.

---

[1.1.0]: https://github.com/fantasylab/aurora/releases/tag/v1.1.0
[1.0.0]: https://github.com/fantasylab/aurora/releases/tag/v1.0.0

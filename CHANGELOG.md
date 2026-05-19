# Changelog

All notable changes to Aurora are documented here. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v1.2 substantially shipped + v2.0 actively shipping

The post-launch sprint that turns Aurora from "the substrate is shipped" into "the platform is shipped." The v1.2 sprint list is mostly live; v2.0 is actively shipping with 9 streams already on `main` (causal inference, multi-dataset joins, composable findings, Plugin SDK, custom KB ingestion, bundle attestation, KB marketplace, Kafka + Postgres CDC streaming connectors, GPU embeddings device selection).

### Added

#### Analytical methods (7 new — catalogue 17+ → 24+)

- **VAR** (`fantasyai/aurora/math/methods/var.py`) — vector autoregression on multivariate numeric data; reports chosen lag, strongest cross-coupling, and forecast horizon
- **DTW** (`fantasyai/aurora/math/methods/dtw.py`) — dynamic time warping; surfaces most-similar + most-dissimilar column pairs
- **BOCPD** (`fantasyai/aurora/math/methods/bocpd.py`) — Bayesian online change-point detection; reports change-point indices with posterior probability
- **Robust PCA** (`fantasyai/aurora/math/methods/robust_pca.py`) — L+S decomposition; reports low-rank estimate + outlier rows + convergence
- **EMD** (`fantasyai/aurora/math/methods/emd.py`) — empirical mode decomposition; reports IMF count + energy shares + dominant period
- **Kalman** (`fantasyai/aurora/math/methods/kalman.py`) — state-space filter on the target column; reports noise-reduction fraction + forecast
- **Spectral entropy** (`fantasyai/aurora/math/methods/spectral_entropy.py`) — global H + regime classification + biggest window jump

All seven are wired into the runner via `extended_runner.py`, persisted as `extended_methods.json` per run, projected through `state_builder` into `state["extended_methods"]`, and rendered as 7 dedicated tiles in the ADVANCED METHODS grid (always-on; missing methods show as "pending"). Honest skip reasons + crash isolation per method.

#### Streaming / continuous mode (Stream 1.4 Phase 1 + 2)

- **`fantasyai/aurora/streaming/`** new package:
  - `watcher.py` — polling-based `FileWatcher` (no `watchdog` dep)
  - `rolling_window.py` — thread-safe `RollingWindowState` with count or time eviction
  - `events.py` — `StreamEventBus` with bounded per-subscriber queues + heartbeat
  - `runner.py` — `IncrementalRunner` composing watcher + window + analysis
  - `dedupe.py` — `FindingDedupeStore` (bounded LRU of finding-identity hashes)
  - `contracts_bridge.py` — evaluates loaded Decision Contracts against streaming findings
- **Endpoints**: `/api/stream/start`, `/api/stream/stop`, `/api/stream/status`, `/api/stream/events` (Server-Sent Events with 30 s heartbeats)
- **Phase 2** fires one `new_finding` event PER genuinely-new finding (not a rollup per poll); opt-in `fire_contracts: true` attaches the contracts bridge so matching contracts fire webhook/log/file actions on live findings
- **Studio popover** with watch path / glob / poll interval, opt-in contracts toggle, status box (running / window rows / dedupe size / contracts attached), and a Live Findings strip (8 most-recent, color-coded by severity)
- 17 streaming tests + 11 Phase 2 dedupe + bridge tests

#### Aurora Cloud (Stream 2.4 Phase 1 + 2)

- **Dockerfile** (multi-stage, non-root `aurora:aurora`, healthcheck on `/api/state`)
- **docker-compose.yml** — one-command local run with bind-mounted persistence
- **`.dockerignore`** + **`.env.example`** — template for `AURORA_LLM_PROVIDER` + credentials
- **`fantasyai/aurora/llm/`** new package — pluggable provider abstraction with 5 backends (Anthropic / OpenAI / Gemini / Ollama / OpenAI-compatible) + None default. Zero new runtime deps (urllib only). Cached singleton factory. `/api/llm/status` endpoint.
- **`fantasyai/aurora/auth/`** new package (Phase 2):
  - `tokens.py` — Bearer-token resolution. Tokens via `AURORA_TOKEN_<workspace>` env vars or a `AURORA_TOKEN_FILE` JSON. Three transports: `Authorization: Bearer …`, `X-Aurora-Token`, `?token=…`.
  - `workspaces.py` — strict path-injection-guarded workspace directory layout under `$AURORA_DATA_ROOT/workspaces/<id>/{runs,kb,contracts,uploads}/`
  - `usage.py` — append-only `usage.jsonl` per workspace + summarise helper
- **`@app.before_request`** middleware resolves the workspace context, records usage, and 401s when `AURORA_AUTH_REQUIRED=1` and auth fails. Health/index endpoints bypass.
- **Endpoints**: `/api/auth/whoami`, `/api/auth/usage`
- **Studio workspace identity chip** in the top toolbar (hidden in single-tenant deploys; click for usage summary popup)
- 16 auth/workspace/usage tests
- **`docs/cloud-deploy.md`** with Phase 1 (Fly / Railway / Render / VPS recipes) + full Phase 2 section (auth setup, workspace isolation, usage logging, what's still Phase 3)

#### Composable findings (Q3 Stream A)

- **`fantasyai/aurora/composable/`** new package:
  - `extractor.py` — `extract_prior_pack(run_dir)` produces a compact prior pack (physics best-fit law, regime structure, baseline range, anomaly z-floors)
  - `applier.py` — `apply_prior_pack_to_findings(findings, pack)` tags findings with a `prior_source` decoration (`matches` / `drifts` / `novel`) based on per-prior comparators
- **Endpoints**: `/api/priors/from_run`, `/api/priors/list`, `/api/priors/inherit`
- **`/api/run`** accepts `inherit_from` and stages the source's pack into the new run dir (both sync and async paths)
- **state_builder** exposes `state["composed_from"]` with the count of priors loaded + findings tagged
- **Studio INHERIT picker** chip in the top toolbar; PRIOR badge on aligned findings (colour-coded by alignment kind)

#### Multi-dataset joins (Q3 Stream B)

- **`fantasyai/aurora/joins/`** new package — `compute_join_report(run_a, run_b)` produces shared keys (by name match + heuristics), schema compatibility (row counts, cadence), cross-correlation hints (from per-run anomaly attribution), and inheritance candidates (physics law / regime K suggestions in both directions)
- **Endpoint**: `/api/joins/analyze`
- **Studio JOIN RUNS** popover with two run selectors; rendered report shows schema compat banner + shared-keys table (with overlap-quality %) + cross-correlation hints + inheritance candidates with rationales

#### Plugin SDK (Q3 Stream C)

- **`fantasyai/aurora/plugins/`** new package — discovers third-party methods via the `aurora_plugins` entry-point group, validates each emitted finding against the contract (`fabricated=True` is hard-refused), catches plugin crashes, emits synthetic "plugin crashed" findings on failure
- **`extended_runner`** invokes loaded plugins on every run; their findings flow alongside the 7 built-in v1.2 methods
- **Endpoint**: `/api/plugins`
- **Studio PLUGINS panel** — chip shows loaded count; popover lists each plugin's load state + version + last-run telemetry
- **`docs/plugins.md`** — full authoring guide (entry-point + finding contract + lifecycle + frontend hooks)

#### Preflight data-quality (the "7th-lens" idea)

- Schema validation + missingness pattern detection + irregular-sampling check
- **Endpoints**: `/api/preflight`, `/api/run/preflight`
- **Studio "data ok / N issues" pill** next to the fabricated chip; click for an expandable findings panel
- 34 preflight tests
- **`docs/preflight.md`**

#### Jupyter / Notebook integration

- `aurora.run(df)` accepts a DataFrame directly (in addition to a path)
- Rich HTML reprs on `Bundle`, `Findings`, `ForecastView`
- `to_html_report()` exports a self-contained HTML
- Sample notebook + 23 tests
- **`docs/jupyter.md`**

#### Knowledge bank pack distribution

- Manifest format + downloader + installer + registry auto-detection
- 4 starter packs (Climate / Finance / Biomed / Industrial)
- Cloudflare R2 primary + HuggingFace mirror
- 25 tests
- **`docs/kb-packs.md`**

#### Decision Contracts — Slack / Discord / Email action types (v1.2)

- **`SlackAction`** — POST to an Incoming Webhook URL. Block-kit payload with header + section fields + plaintext fallback. HTTPS-only. SSRF guard via the same `_is_private_or_local` check the webhook action uses. URL token segment redacted in audit serialisation.
- **`DiscordAction`** — POST to a Channel Webhook URL. Embed-formatted with title + 4 fields (contract / trigger / run / bundle hash). Same security guards as Slack.
- **`EmailAction`** — SMTP via stdlib `smtplib`. TLS required (port 587 STARTTLS or 465 SMTPS); plain SMTP refused unless `AURORA_ALLOW_PLAINTEXT_SMTP=1`. Credentials from `AURORA_SMTP_USER` / `AURORA_SMTP_PASS` env vars — never the contract document. Subject + body support templating with safe placeholders. Recipients capped at 20.
- 20 tests covering formatter shape, URL redaction, SSRF refusal, factory dispatch, SMTP STARTTLS + SMTPS paths, recipient cap, template safety.
- `docs/decision-contracts.md` updated with full schemas + security guards.

#### Runs Library UI (v1.2)

- **`fantasyai/aurora/runs_library/`** new package:
  - `pinned.py` — workspace-isolated `pinned_runs.json`; idempotent pin/unpin; 100-run cap.
  - `compare.py` — `compare_runs(a, b)` produces summary delta + new/disappeared findings using the streaming dedupe hash so identity is consistent across batch + streaming flows. When the runs cover different datasets, embeds the Q3 join report.
  - `share.py` — exports a portable `.aurora.json` to a workspace-isolated share directory. `public_url` field reserved for Aurora Cloud Phase 3.
- Endpoints: `/api/runs/{pinned,pin,unpin,compare,share}`. `/api/runs` annotates each entry with `pinned: bool`.
- Studio **`runs: N`** chip in the top toolbar; popover lists pinned-first with per-row actions (load / pin / unpin / share / select for A/B) + a comparison report panel.
- 16 tests covering pin roundtrip + idempotency + workspace isolation + cap, compare delta + cross-dataset join attachment + new/disappeared diff, share bundle export.
- `docs/runs-library.md`.

#### MCP HTTP transport (v1.2)

- **`aurora_mcp/http_server.py`** — Flask blueprint exposing `GET /tools`, `POST /tools/<name>`, `POST /initialize`, `GET/POST /allowed_roots` over JSON. Same contract as stdio: tools never raise across the boundary (crash → 200 + `error_kind: tool_crash`), output capped at `MAX_RESPONSE_BYTES`, path allowlist enforced.
- Two deployment modes:
  - Mounted under Studio at `/mcp/v1/*` (auto-mounted when `studio_api.py` boots); inherits Studio's auth middleware.
  - Standalone (`python -m aurora_mcp.http_server --port 8765`) with optional `AURORA_MCP_HTTP_TOKEN` bearer-token gate.
- `/api/mcp/status` reports mounted state.
- 12 tests covering discovery, tool calls, JSON error envelope on crash, oversize truncation, all 3 token-gate paths, `allowed_roots` get/post.
- `docs/mcp.md` updated with the "HTTP transport (v1.2)" section.

#### Causal inference (v2.0 Stream A)

- **`fantasyai/aurora/causal/`** new package: `dag.py`, `do_calculus.py`, `counterfactual.py`.
- `CausalDAG` reads the system_model artifact, breaks cycles with `DagCycleWarning`, implements d-separation + backdoor identification.
- `do_query()` fits a backdoor-adjusted OLS regression. On a synthetic 800-row confounded SCM it recovers true β=0.4 within ~0.05.
- `counterfactual_query()` does Pearl's three-step recipe via the linear SCM shortcut.
- The legacy WHAT-IF panel now ALSO calls do_query in the background and renders a "do() causal verdict — backdoor adjustment" block beneath the simulator output. If the effect isn't identifiable, the verdict says so honestly.
- Endpoints: `/api/causal/{dag,identify,do,counterfactual}`.
- v2.0 LAB → Causal tab with treatment / outcome / intervention inputs + three action buttons.
- 12 tests — DAG primitives, cycle-breaking, d-separation, backdoor admissibility, estimation accuracy, counterfactual recipe.
- `docs/causal-inference.md`.

#### Streaming connectors (v2.0 Stream B)

- **`fantasyai/aurora/streaming/connectors/`** — `StreamingConnector` base class + `KafkaConnector` + `PostgresCDCConnector`. Both deps gated: `kafka-python` and `psycopg` stay optional. `availability()` classmethod reports installed/not-installed + install hint.
- Postgres CDC uses simple-CDC (numeric id or timestamp watermark) — no logical-replication-slot setup required. Identifier whitelist guards against SQL injection. Watermark persisted per-workspace.
- Both connectors share batching + ingest path via the base class; config summary redacts credentials.
- Endpoint: `/api/stream/connectors`.
- v2.0 LAB → Connectors tab.
- 14 tests — availability, dep-gating, config validation, identifier guards, password redaction, base-class ingest path.

#### Custom KB ingestion (v2.0 Stream C)

- **`fantasyai/aurora/kb/ingest/`** — `pdf_ingester.py`, `folder_ingester.py`, `storage.py`.
- PDF extraction uses `pypdf` when installed; falls back to plain-text neighbour files (`paper.txt`) when not. Encrypted PDFs raise a clear error.
- `ingest_folder()` walks `.pdf` / `.txt` / `.md` files, chunks them at paragraph boundaries (~4000 chars), writes a workspace-isolated `custom_entries.jsonl`. Idempotent — re-ingesting produces stable `seed_id`s.
- Endpoints: `/api/kb/ingest/{folder,list}`.
- v2.0 LAB → KB Ingest tab with a folder path input + native folder picker (`<input webkitdirectory>`) + drag-and-drop zone. Honest UX caveat: browsers don't expose absolute folder paths, so the picker seeds the folder name + asks the user to type the absolute path.
- 7 tests — chunking strategy, text-neighbour fallback, folder walk, idempotency, workspace isolation.

#### Bundle attestation (v2.0 Stream D)

- **`fantasyai/aurora/attestation/`** — `verify.py`, `registry.py`.
- Three-check rollup: integrity (recomputed content hash) + signature (Ed25519 via the SDK) + signer trust (workspace-isolated registry).
- `AttestationReport.summary` is True ONLY when all three pass — so a bundle that verifies but whose signer isn't registered yields `summary=False, untrusted_signer=True` (the right answer for "stranger sent you a bundle").
- Trusted-signer registry per workspace, 32-byte Ed25519 public keys validated at write time.
- Endpoints: `/api/attest/{verify,signers,signers/remove}`.
- v2.0 LAB → Attest tab.
- 9 tests — registry CRUD, key validation, workspace isolation, attestation with/without sig + with/without trust + integrity failures.

#### KB pack marketplace (v2.0 Stream E)

- **`fantasyai/aurora/kb/marketplace.py`** — listing API that reads the bundled manifest + cross-references the local installed-pack registry.
- Each entry carries `install_state`: `available` / `installed` / `upgrade_available` / `preview` / `failed`.
- **`preview`** state added for manifest packs whose SHA is `PENDING_FIRST_BUILD` — listed in the manifest as reservation slots but not yet released. Frontend renders them with a disabled install button + warning instead of a silent fail.
- Real install flow now correctly chains `download_pack → install_pack` with stage-tagged structured errors so the frontend can surface "downloading + verifying SHA-256…" inline status.
- Endpoints: `/api/kb/marketplace`, `/install`, `/uninstall`.
- v2.0 LAB → Marketplace tab.
- 5 tests — listing shape, preview-state detection, install error envelopes.

#### GPU embeddings device gate (v2.0 Stream F)

- **`fantasyai/aurora/embedding_device.py`** — `get_embedding_device()` with `AURORA_EMBEDDINGS_DEVICE` override (cpu / cuda / mps / auto).
- Auto-detects CUDA → MPS → CPU. Graceful one-shot warning if the requested device isn't actually available. No eager torch import.
- Endpoint: `/api/embeddings/status`.
- v2.0 LAB → Compute tab shows preferred + chosen device + torch version + CUDA/MPS availability.
- 5 tests — explicit cpu, unknown-value fallback, auto resolution, cache + refresh, info-dict shape.

#### v2.0 LAB modal (UX)

- Single discoverability surface for the 9 v2.0 streams.
- Top-toolbar **`⚡ v2.0 LAB · NEW · 6`** chip with cyan accent + linear gradient background.
- Opens as a full-screen modal (1100 × 720 max, 92vw × 86vh fluid) with sidebar nav + content area. Two close paths (Escape key, backdrop click); body scroll locked while open; focus auto-jumps to the first input.
- Six tabs (Causal · KB Ingest · Attest · Marketplace · Connectors · Compute), each with a header + intro paragraph above the controls.

### Changed

- **`extended_runner`** now also invokes installed plugins after running built-ins; their findings flow into the same `findings` list and per-plugin telemetry surfaces under `result["plugins"]`
- **`state_builder`** always emits the 7-tile shell for `state["extended_methods"]`, even when `extended_methods.json` is missing (legacy run dirs). Missing methods show as `status: "missing"` so the frontend grid stays consistent.
- **`/api/stream/status`** now exposes `path`, `window_rows`, `dedupe_size`, `contracts_attached` so the Studio popover can render rich state
- **CI workflows** scope to a focused, fast test list; tests landing post-launch (streaming, auth, Q3) are wired into both `ci.yml` and `tests.yml`

### Fixed

- **`studio_api.py` `api_stream_events()` SyntaxError** — `global _STREAM_BUS` was declared after a reference; reordered
- **`/api/run/preflight`** is the per-run-quality preflight; `/api/preflight` is the runtime data-quality lens (separate concerns)
- **Frontend cache-key bug** in the data-quality controller — was keyed on `window.location.pathname` (constant) so only the first run ever fetched preflight. Now keyed on run-dir + supports pending/unknown visual states.
- **Frontend `extended_methods` gate** — old version required `available && per_method` so legacy runs showed no tiles at all. Now always renders the 7-slot grid with "pending" cards.

### Security

- Aurora Cloud Phase 2 auth: bearer-token validation, workspace isolation under `$AURORA_DATA_ROOT/workspaces/<id>/`, strict path-injection guards on workspace ids
- Plugin SDK: `fabricated=True` is refused at the validation gate; plugin crashes become synthetic "failed" findings, not silent corruption
- Streaming bridge: contract evaluation runs in a try/except so a buggy contract never takes down the runner

### Tests

- **599 tests passing locally** (~625 in CI, which adds scipy + flask-dependent tests). Baseline at v1.1 launch was 320.
- New test files since launch:
  - `tests/test_preflight.py` (34)
  - `tests/test_kb_packs.py` (25)
  - `tests/test_aurora_jupyter.py` (23)
  - `tests/test_new_methods.py` (36 — 31 method tests + 5 orchestrator)
  - `tests/test_llm_providers.py` (16)
  - `tests/test_streaming.py` (17)
  - `tests/test_streaming_phase2.py` (11)
  - `tests/test_auth_workspaces.py` (16)
  - `tests/test_q3_composable_joins_plugins.py` (17)
  - `tests/test_contract_action_types.py` (20) — Slack / Discord / Email
  - `tests/test_runs_library.py` (16) — pin / compare / share
  - `tests/test_mcp_http_transport.py` (12) — HTTP transport + token gate
  - `tests/test_q2_causal_inference.py` (12) — do-calculus + counterfactuals
  - `tests/test_q2_streaming_connectors.py` (14) — Kafka + Postgres CDC
  - `tests/test_q2_kb_attest_market_embed.py` (24+) — ingestion + attestation + marketplace + embeddings
  - `tests/test_v1_2_frontend_wiring.py` (35) — full v2.0 LAB modal audit

### Docs

- **`README.md`** updated for v1.2 + v2.0 surfaces + Docker quickstart + 599-test badge
- **`ROADMAP.md`** restructured with explicit `✅ / 🟡 / ⏳` status per item; v2.0 streams promoted to "shipped ahead of schedule"
- **`docs/streaming.md`** — streaming mode (Phase 1 + 2)
- **`docs/cloud-deploy.md`** — full Phase 1 + 2 self-host guide
- **`docs/plugins.md`** — Plugin SDK authoring + contract + lifecycle
- **`docs/preflight.md`** — data-quality lens
- **`docs/kb-packs.md`** — knowledge bank pack distribution
- **`docs/jupyter.md`** — notebook SDK surface
- **`docs/new-methods.md`** — the 7 v1.2 extended methods
- **`docs/decision-contracts.md`** — extended with Slack / Discord / Email action types + security guards
- **`docs/mcp.md`** — extended with the HTTP transport section (mounted vs standalone)
- **`docs/runs-library.md`** — pin / A-B compare / share workflows
- **`docs/causal-inference.md`** — do-calculus + counterfactual queries reference

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

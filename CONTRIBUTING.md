# Contributing to Aurora

Thanks for your interest in contributing. Aurora is built in public with help from contributors like you; every meaningful PR moves the project forward.

## Ways to Contribute

- **Bug reports** — open an issue with a clear repro
- **Feature requests** — open an issue describing the use case
- **Code contributions** — see "Development Workflow" below
- **Knowledge bank entries** — see [docs/knowledge-bank.md](docs/knowledge-bank.md)
- **Decision Contract recipes** — share working contracts for common use cases
- **MCP demos** — show Aurora-as-tool in your AI agent of choice
- **Documentation improvements** — typos, clarifications, new examples all welcome
- **Test datasets** — share interesting datasets that surface edge cases

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Short version: be kind, be specific, assume good faith.

## Development Workflow

### Setup

```bash
git clone https://github.com/fantasylab/aurora.git
cd aurora
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # if present; else dev deps are in requirements.txt
```

Optional but recommended:

```bash
pip install cryptography             # enables Ed25519 signing for Aurora Bundles
pip install mcp                       # enables `python -m aurora_mcp.server`
```

### Run tests

```bash
pytest                                       # full suite
pytest -m "not slow"                         # fast tests only
pytest tests/test_aurora_sdk.py              # single file
pytest tests/test_aurora_mcp.py              # MCP tools
pytest tests/test_decision_contracts.py      # contracts engine
pytest tests/test_research_kit.py            # research kit
```

The full focused suite is 320 tests as of v1.1. PRs should not introduce new failures.

### Code style

- Python 3.10+ syntax
- Black for formatting (`black .`)
- Type hints on public APIs
- Docstrings on every public function (Google style)
- All `try/except` blocks must log `traceback.format_exc()` — no silent failures
- New features need new tests
- Public-facing modules (SDK, MCP, Contracts, Research Kit) must keep their JSON schemas backward compatible within a major version

### Submitting a PR

1. Fork the repo
2. Create a branch (`feature/your-feature` or `fix/your-fix`)
3. Make changes with tests
4. Run `pytest` and confirm green
5. Run `black .` and `ruff .` if available
6. Commit with a clear message
7. Open a PR against `main` with description of changes

PRs are reviewed within a week typically. Small, focused PRs land faster than large ones.

## Architectural Principles

Before contributing significant features, please read [ARCHITECTURE.md](ARCHITECTURE.md). Key principles:

1. **Local-first.** No code may introduce required cloud dependencies.
2. **Glass-box.** No silent failures. No hidden state. No undocumented heuristics.
3. **Anti-hallucination.** The LLM never makes analytical decisions.
4. **Deterministic.** Same input + same seed = same output. The Aurora Bundle's content hash is the proof.
5. **Honest reporting.** When methods sample or time out, the user must be told.
6. **Substrate-shaped.** Outputs are first-class artifacts other tools can consume.

PRs that violate these principles will be requested to revise or rejected.

## Specific Areas Where Help is Welcome

- **Knowledge bank expansion** — cited entries for new domains (biomed, energy, climate, finance, materials science…)
- **New analytical methods** — implementations with tests, citations, glass-box compliance, and clear skip conditions
- **MCP integrations** — example notebooks showing Claude Desktop / Cursor / custom agents calling Aurora tools
- **Decision Contract actions** — Slack, Discord, PagerDuty, email, database adapters (each with security review)
- **Frontend polish** — accessibility, mobile responsiveness, internationalisation
- **Documentation** — tutorials, walkthroughs of specific use cases, video demos
- **Test datasets** — interesting, complex datasets that exercise edge cases
- **Translations** — Aurora is currently English-only; help internationalising is welcome

## Special Note for SDK / MCP / Contracts Contributors

The substrate layers are public API. Backward compatibility within a major version is a hard contract:

- **Aurora Bundle Format**: adding new top-level keys is fine; renaming / removing requires a major version bump
- **MCP tool signatures**: adding optional input fields is fine; changing required fields requires a major version bump
- **Decision Contract operators**: adding new operators is fine; changing semantics of an existing operator requires a major version bump
- **Research Kit outputs**: file names + section names are stable; content layout can evolve

When in doubt, file an issue first so we can discuss whether your change should land in the current major or queue for the next one.

## Questions

Open a [Discussion](https://github.com/fantasylab/aurora/discussions) or ping [@Fantasylab_ai](https://twitter.com/Fantasylab_ai) on Twitter.

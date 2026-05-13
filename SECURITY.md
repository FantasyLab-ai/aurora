# Security Policy

Aurora is local-first by design — your data, your CPU, your runs. That stance is also Aurora's primary security posture: most attack surfaces that affect cloud SaaS products don't apply here because Aurora doesn't host your data.

This document covers the security model, what we promise, what's out of scope, and how to report a vulnerability.

## Supported Versions

We provide security updates for the latest minor release. Older versions are not actively maintained.

| Version | Supported |
|---|---|
| 1.x | ✅ |
| < 1.0 | ❌ |

When a security update lands, we ship a patch release (e.g., 1.1.1) and publish an advisory in [GitHub Security Advisories](https://github.com/fantasylab/aurora/security/advisories).

## Reporting a Vulnerability

**Please report security vulnerabilities privately — do not open a public issue.**

📧 **security@fantasylab.ai**

Include:
- A clear description of the vulnerability
- Steps to reproduce (or proof-of-concept code)
- Affected version(s)
- Your assessment of impact (information disclosure, RCE, DoS, etc.)
- Whether you'd like to be credited in the advisory

We respond within **72 hours** with an acknowledgement and a timeline. Responsible-disclosure practice is appreciated; we treat reporters as collaborators, not adversaries.

GitHub's private vulnerability reporting is also enabled on this repo as a backup channel: [Report a vulnerability](https://github.com/fantasylab/aurora/security/advisories/new).

## What's in Scope

Aurora's security-critical surfaces, in roughly decreasing order of impact:

1. **MCP server path-allowlist bypass** — if a crafted argument lets a tool read files outside `--allow-root`, that's a high-severity bug.
2. **Decision Contracts SSRF** — if a webhook URL bypasses the private-IP / loopback guard without `AURORA_ALLOW_LOCAL_WEBHOOKS=1`, that's high-severity.
3. **Decision Contracts file-action escape** — if a relative path manages to write outside `AURORA_CONTRACTS_OUTPUT` after `Path.resolve()`, that's high-severity.
4. **Aurora Bundle tamper without detection** — if you can mutate a bundle's content without changing its `content_hash`, that breaks the trust contract; high-severity.
5. **Aurora Bundle signature forgery** — if you can construct a valid Ed25519 signature without the private key, that's a cryptography bug we'd report upstream too.
6. **Pipeline crash on malformed input** — a CSV that crashes Aurora is a DoS-class bug; medium severity (Aurora processes user-controlled inputs by design).
7. **Knowledge bank tampering** — replacing entries in the local SQLite without the user noticing.
8. **Dependency vulnerabilities** — Aurora ships with `requirements.txt`; CVEs in those deps are tracked.

## What's Out of Scope

- **Issues that require an attacker to already have shell access to your machine.** Aurora is local-first; anyone with shell access can already read your files. Aurora doesn't claim to defend against that.
- **Crashes from malformed inputs that surface honestly via the existing error banner.** Aurora is meant to refuse bad input gracefully; if it does so visibly (error banner, "couldn't profile dataset" message), that's working as designed.
- **Per-method timeouts and sampling are features, not bugs.** Aurora reports them honestly to the user.
- **LLM hallucinations in synthesis when the verifier is disabled.** Aurora ships with strict RAG + post-hoc verification on by default; disabling them is the user's choice.
- **The user's own LLM client sending Aurora's output to a cloud LLM provider.** Aurora keeps data local; the user's choice of LLM client (Claude Desktop, Cursor, etc.) is between them and their LLM vendor.

## Aurora's Threat Model

We design Aurora assuming:

- The user *trusts the Aurora source code they're running.* Open source means anyone can audit; we recommend reading the code if you're deploying in a sensitive environment.
- The user *trusts their own machine.* If your laptop is compromised, no application can protect you.
- The user *does NOT trust* arbitrary files dropped on Aurora's input. Aurora is robust against malformed CSVs / JSON / Parquet — these don't escape into code execution.
- The user *does NOT trust* arbitrary LLM agents that call Aurora via MCP. The path allowlist, output cap, and JSON-only tool responses are the perimeter.
- The user *does NOT trust* arbitrary webhook endpoints — Decision Contracts validate URLs against SSRF before firing.

## Security Controls We've Built

### Aurora Bundle integrity
- SHA-256 content hash computed over a canonical JSON serialisation
- Optional Ed25519 signing (`bundle.sign(private_key_bytes)`) when `cryptography` is installed
- `verify()` raises `BundleIntegrityError` on any mismatch; the chip in the Studio (`0 fabricated`) is the live signal

### MCP server
- Path allowlist enforced per tool call via `Path.resolve()` + ancestor check; symlink escapes blocked
- Output capped at 2 MB (`MAX_RESPONSE_BYTES`) so a runaway agent can't exhaust the client
- All errors wrapped as `{"error", "error_kind"}` — tools never raise across the MCP boundary
- No shell, no `eval`, no `exec`, no subprocess spawn

### Decision Contracts
- Webhook URLs: only `http(s)` schemes; hostname resolved + checked against private / loopback / link-local / multicast / reserved IPs
- Override via `AURORA_ALLOW_LOCAL_WEBHOOKS=1` for testing only (a deliberate friction point, not a default)
- Authorization / X-API-Key / Cookie headers redacted in audit records (`_redact_auth`)
- 1 MB webhook body cap, 30 s timeout cap
- File actions: relative paths only; `..` blocked; resolved-path-must-stay-under-root double-check; 100 MB file size cap
- Rate-limited per-contract (`max_per_minute|hour|day`)

### Pipeline robustness
- Every method wrapped in a 90 s timeout (configurable)
- Stratified time-preserving sampling preserves extreme outliers (|z| ≥ 4)
- Malformed inputs surface as visible error banners, never silent failures

## Deployment in Regulated Environments

If you're deploying Aurora in healthcare, finance, defence, or another regulated environment, please review [docs/deployment.md](docs/deployment.md) for the recommended configuration:

- Air-gapped install (download knowledge bank on a connected machine, copy across)
- Local LLM only (no Ollama → OpenAI proxy)
- Signed bundles for every analysis (Ed25519, key custody per your standard)
- Audit log streaming via Decision Contracts (`file` action writes JSONL; ship to your SIEM)
- Restricted Allow-root (`--allow-root` scoped to a per-project directory)

For commercial support in regulated environments — including signed-bundle attestation service, custom Decision Contract actions, hosted knowledge bank sync, SLA — contact **enterprise@fantasylab.ai**.

## Cryptographic Notes

- **Hashing:** SHA-256 (`hashlib.sha256`); 32-byte output; chosen for ubiquity + collision resistance for our threat model (tamper detection, not adversarial cryptography).
- **Signing:** Ed25519 via [PyCA cryptography](https://cryptography.io/); we never roll our own crypto.
- **Key custody:** out of scope — Aurora doesn't store private keys. Users supply 32-byte raw private key bytes to `bundle.sign()`. Most deployments use a hardware token or secrets manager and call `sign()` from a wrapper.

## Acknowledgements

Security researchers who report vulnerabilities responsibly are credited in the corresponding GitHub Security Advisory (unless they prefer to remain anonymous).

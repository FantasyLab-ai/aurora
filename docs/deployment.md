# Deployment

This guide covers deploying Aurora beyond the default "single-user local Studio." Aurora's primary mode is local, but several deployment shapes are worth documenting:

1. **Local single-user** — the default; covered in [docs/getting-started.md](getting-started.md)
2. **Local team** — one Aurora install shared by a small team via a local network
3. **Air-gapped / regulated** — no internet egress at all
4. **MCP-headless** — Aurora exposed only via MCP, no Web Studio
5. **Enterprise customer-hosted** — coming in v2.0

## Local single-user

Already covered in [Getting Started](getting-started.md). Run `python studio_api.py`, open `http://127.0.0.1:8000`. Aurora binds to localhost only — no other machine can reach it.

## Local team

If a small team wants to share one Aurora instance on a local network:

### Steps

1. Install Aurora on a shared workstation (or a small Linux server on the LAN)
2. Start with a bind on all interfaces:

   ```bash
   AURORA_HOST=0.0.0.0 AURORA_PORT=8000 python studio_api.py
   ```

3. Generate an admin token:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

4. Set it before starting:

   ```bash
   AURORA_ADMIN_TOKEN=<paste-token> python studio_api.py
   ```

5. Each user passes `Authorization: Bearer <token>` on write endpoints. The Studio UI prompts for it on first load and stores it in localStorage.

### Recommendations

- Put Aurora behind a reverse proxy with TLS (Caddy, Nginx, Traefik)
- Use a real identity provider (oauth2-proxy in front of Aurora) — Aurora trusts whatever identity the proxy passes via headers
- Mount knowledge bank on shared storage if you want team-wide priors
- Mount `outputs/aurora_dataset_runs/` on shared storage so runs persist across restarts

### What this is NOT

This is not a multi-tenant SaaS. Aurora has no user-level isolation. Anyone with the bearer token can read any run on the instance. For per-user isolation, run one Aurora process per user behind your IdP — each process gets its own port and its own user-data directory.

## Air-gapped / regulated environments

Aurora's local-first stance makes air-gapped deployment straightforward:

### Pre-deployment (one-time, on a connected machine)

1. Clone the repo + `pip install` requirements
2. Download the full knowledge bank: `python scripts/download_knowledge_bank.py`
3. Pull the LLM model: `ollama pull gemma3:12b`
4. Tar up the entire `aurora/` directory, the `.venv/`, the `~/.aurora/` user-data, and the `~/.ollama/` model cache
5. Transfer via approved channel (USB, SFTP, removable media)

### Post-deployment (on the air-gapped target)

1. Restore the tarball to matching paths
2. Verify the bundle integrity via `sha256sum` against the source machine's hashes
3. Run `python studio_api.py`

### Configuration

- Set `AURORA_OFFLINE=1` to assert no network access expected (Aurora will refuse to attempt knowledge bank updates)
- Set `OLLAMA_HOST=127.0.0.1:11434` (or your local Ollama bind)
- Disable any auto-update jobs in the OS

### Audit trail

For regulated environments, configure Decision Contracts to log every Aurora run:

```json
{
  "id": "audit-everything",
  "name": "Append every Aurora run to the audit log",
  "trigger": {"field": "findings.count", "op": ">=", "value": 0},
  "actions": [
    {"type": "file", "path": "audit-log.jsonl", "include_metadata": true}
  ]
}
```

This writes a JSON-lines record per run to `AURORA_CONTRACTS_OUTPUT/audit-log.jsonl`. Ship to your SIEM via a sidecar (Filebeat, Fluentd, etc.).

For tamper-evident analysis records, sign each bundle:

```python
from aurora_sdk import run
r = run("data.csv")
r.bundle.sign(private_key_bytes)         # private key from your secrets manager
r.bundle.save(f"audit/{r.bundle.run_id}.aurora.json")
```

The Ed25519 public key goes in your audit policy; auditors verify with `bundle.verify(require_signature=True)`.

## MCP-headless

For agent-only deployments — no Web Studio — just run the MCP server:

```bash
python -m aurora_mcp.server \
  --allow-root /var/aurora/datasets \
  --allow-root /var/aurora/outputs
```

Configure your agent (Claude Desktop, Cursor, custom) to spawn the server via stdio. The Web Studio code is still in the repo but never serves HTTP if you don't run `studio_api.py`.

This is the recommended deployment for systems where Aurora is purely a tool other software calls.

## Enterprise customer-hosted (v2.0)

Coming in v2.0:

- Docker image + Helm chart for Kubernetes deployment
- Customer-managed knowledge bank sync (private k-bank packs)
- Signed-bundle attestation service (one-call verification)
- Multi-user with first-class identity, audit-log streaming
- SLA + dedicated support

Interested in early access? Email **enterprise@fantasylab.ai**.

## Performance tuning

### Memory

Default footprint: 200–500 MB peak. Tune via:

- `AURORA_MAX_ROWS_FULL_TIER=<N>` — beyond this row count, AUTO will sample for tier-2/3 methods (default 100 K)
- `AURORA_KB_CACHE_SIZE_MB=<N>` — knowledge bank vector-index cache (default 256)

### CPU

Methods that benefit from parallelism (HMM, GP, persistent homology, SINDy) will use `os.cpu_count()` threads by default. Limit via `AURORA_THREADS=<N>` if Aurora is one of multiple workloads on the box.

### GPU

If a CUDA-capable GPU is present and Ollama is configured to use it, synthesis is 3–10× faster. Aurora itself doesn't currently use the GPU; future versions will add GPU embedding inference.

### Disk

- Application: ~500 MB
- Full knowledge bank: ~2 GB
- Per-run outputs: 1–50 MB depending on dataset size + tier

Outputs accumulate. Run `python scripts/prune_old_runs.py --keep-last 100` periodically (or set up a cron).

## Monitoring

The studio API logs to stderr by default. Recommended for production:

- Pipe stderr through `journald` (systemd unit) or `syslog`
- Watch for `[ERROR]` lines
- Set up an alert on the `app-phase-running` indicator stalling >10 min (means a method exceeded its timeout *and* the pipeline didn't recover)
- Health endpoint: `GET /api/health` returns `{"ok": true, "version": "..."}`

## Security checklist

Before going to production:

- [ ] Bind to localhost-only unless behind a reverse proxy with TLS
- [ ] Set `AURORA_ADMIN_TOKEN` to a random 256-bit token
- [ ] Restrict `--allow-root` on the MCP server to per-project paths
- [ ] Don't set `AURORA_ALLOW_LOCAL_WEBHOOKS=1` in production (it's for dev/test only)
- [ ] Audit `requirements.txt` for CVEs (`pip-audit`)
- [ ] Sign Aurora Bundles for tamper-evident distribution if the analysis matters
- [ ] Configure Decision Contracts file-action sandbox (`AURORA_CONTRACTS_OUTPUT`) to a directory only Aurora can write to
- [ ] Backup the knowledge bank periodically — losing it means re-downloading

See [SECURITY.md](../SECURITY.md) for the full security model.

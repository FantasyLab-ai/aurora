# Aurora Cloud — Phase 1 self-host

Aurora ships a Docker image so you can run hosted Aurora on your own
infrastructure (laptop, VPS, Fly.io, Railway, Render, a Kubernetes
cluster). Same engine as the local install; same bundle format; same
glass-box guarantees. You bring your own LLM credentials.

This is **Phase 1** of Stream 2.4 in the 12-month roadmap. Phase 2
adds multi-tenant auth + per-user storage + billing; Phase 3 ships a
managed offering at `cloud.aurora.fantasylab.ai`. Both build on this
scaffolding.

## What you need

- Docker + docker-compose (or any container runtime: Podman, k8s, etc.)
- An LLM credential (or be willing to disable synthesis):
  - **Anthropic Claude:** `ANTHROPIC_API_KEY`
  - **OpenAI:** `OPENAI_API_KEY`
  - **Google Gemini:** `GEMINI_API_KEY`
  - **Ollama (local):** running Ollama daemon, reachable from the container
  - **OpenAI-compatible** (Groq, OpenRouter, LM Studio, vLLM, …): URL + key
  - **None:** math runs, narrative paragraph is skipped (totally usable)

## Quickstart (local docker-compose)

```bash
git clone https://github.com/FantasyLab-ai/aurora.git
cd aurora

# Set your preferred LLM provider + credentials
cp .env.example .env  # then edit
#   AURORA_LLM_PROVIDER=anthropic
#   ANTHROPIC_API_KEY=sk-ant-...

docker compose up
# → Aurora Studio at http://localhost:8000
```

Persistent state lands in `./aurora-data/` (KB, run history) and
`./aurora-config/` (per-installation settings). Both stay on your host;
the container is stateless.

## LLM provider quick-config

The container reads `AURORA_LLM_PROVIDER` and picks a backend. Each
backend needs different env vars:

| Provider | Env vars | Default model |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` (+ optional `ANTHROPIC_MODEL`, `ANTHROPIC_BASE_URL`) | `claude-sonnet-4-5` |
| `openai` | `OPENAI_API_KEY` (+ optional `OPENAI_MODEL`, `OPENAI_BASE_URL`) | `gpt-4o-mini` |
| `gemini` | `GEMINI_API_KEY` (+ optional `GEMINI_MODEL`) | `gemini-1.5-flash` |
| `ollama` | `OLLAMA_BASE_URL` (default `http://host.docker.internal:11434`), `OLLAMA_MODEL` (default `gemma3:12b`) | `gemma3:12b` |
| `openai_compatible` | `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_MODEL` | (provider-specific) |
| `none` | — | (synthesis disabled) |

Switch providers anytime by editing `.env` and restarting:
`docker compose down && docker compose up`. No code changes needed.

Verify the current provider via:

```bash
curl http://localhost:8000/api/llm/status
```

## Deploy to a public host

### Fly.io

```bash
# 1) Sign up + install fly CLI
brew install flyctl    # or https://fly.io/docs/hands-on/install-flyctl/
fly auth signup

# 2) From the repo root:
fly launch --dockerfile Dockerfile  # answer the prompts
# When asked about volumes, say YES and let Fly create one for /var/aurora.

# 3) Set your LLM credentials as secrets (never commit them):
fly secrets set AURORA_LLM_PROVIDER=anthropic
fly secrets set ANTHROPIC_API_KEY=sk-ant-...

# 4) Deploy:
fly deploy

# 5) Open the URL Fly prints. Your Aurora is live.
```

### Railway

1. New project → Deploy from GitHub repo
2. Railway auto-detects the Dockerfile
3. Add environment variables (same as the Fly.io list)
4. Add a persistent volume mounted at `/var/aurora`
5. Hit Deploy

### Render

Similar shape — create a Web Service from the repo, set env vars, add
a persistent disk at `/var/aurora`.

### Your own VPS (Hetzner / DigitalOcean / Linode)

```bash
ssh root@your-vps
apt install docker.io docker-compose-plugin
git clone https://github.com/FantasyLab-ai/aurora.git
cd aurora
nano .env   # set provider + credentials
docker compose up -d
```

Then put a reverse proxy (Caddy / nginx) in front to terminate TLS and
forward to `:8000`. Caddy example:

```
aurora.example.com {
    reverse_proxy localhost:8000
}
```

## Security checklist for hosted Aurora

| Item | Phase 1 default | Production hardening |
|---|---|---|
| Auth | None (single-tenant) | Add Caddy basic-auth or an OAuth2 proxy in front |
| HTTPS | None (HTTP only) | Caddy or nginx with Let's Encrypt |
| Credentials at rest | Env vars in `.env` | Use Fly Secrets / Railway env / your platform's secret store |
| Network exposure | All of `0.0.0.0:8000` | Bind to `127.0.0.1` + reverse proxy |
| LLM credential rotation | Manual | Rotate every 90 days; revoke + reissue at the provider |

## What's NOT in Phase 1 (coming in Phase 2/3)

- Multi-user accounts + auth
- Per-user namespacing of run data + bundles
- Subscription / billing
- Team workspaces (shared runs across users)
- API tokens for programmatic access
- An admin UI for managing users and LLM allowances

If you need any of these now, run a single-tenant instance per user.
That model works fine on Fly's free tier for solo users.

## Updating

Aurora versions move forward via the Dockerfile. To update:

```bash
git pull
docker compose build --pull   # rebuild from the new source
docker compose up -d           # rolling restart
```

`./aurora-data/` (your KB + runs) survives the rebuild because it's a
bind mount.

## License

Apache 2.0. Same as Aurora itself.

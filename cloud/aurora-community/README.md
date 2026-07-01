# Aurora Community — hosted findings feed

A tiny Cloudflare Worker + one KV namespace. It is the single source of truth
that makes "Share to community" actually communal: the desktop app POSTs a
shared finding to `/share`, and both the app's **Community** tab and the
marketing landing page read `/feed`.

It's also your **bot-proof usage signal** — clones and views are mostly CI and
scrapers, but a share is a real human who ran a real analysis and chose to post
it. Count of shares + unique sharers is the number that can't be faked.

## Routes

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/` | health / info |
| `GET`  | `/feed?limit=N` | newest shared findings (default 30, max 100) |
| `POST` | `/share` | submit `{title, detail?, method?, dataset?, severity?, run_id?, confidence?}` |
| `DELETE` | `/finding/:id` | admin takedown (needs `Authorization: Bearer <ADMIN_TOKEN>`) |

Only the fields above are stored — the desktop never sends raw data rows.
Findings self-expire after 90 days; writes are rate-limited (20/min per IP),
length-capped, and run through a small content denylist.

## Deploy (one time)

```bash
cd cloud/aurora-community
npm install -g wrangler        # if you don't have it
wrangler login

# 1. Create the KV namespace and paste the printed id into wrangler.toml
wrangler kv namespace create FINDINGS

# 2. (optional) set an admin token for takedowns
wrangler secret put ADMIN_TOKEN

# 3. ship it
wrangler deploy
```

This deploys to **`https://aurora-community.<your-subdomain>.workers.dev`**.
Given your existing `*.fantasy-labai.workers.dev` Worker, that's
`https://aurora-community.fantasy-labai.workers.dev` — which is the default the
desktop app already points at. If you deploy under a different name/subdomain
or a custom domain, set it on the backend instead:

```bash
# the Aurora backend reads this; overrides the baked-in default
export AURORA_COMMUNITY_API="https://aurora-community.<your-subdomain>.workers.dev"
```

## Smoke test

```bash
BASE=https://aurora-community.fantasy-labai.workers.dev
curl -s -X POST "$BASE/share" -H 'content-type: application/json' \
  -d '{"title":"Regime shift at row 412","method":"BOCPD","dataset":"demo.csv","severity":"warn"}'
curl -s "$BASE/feed?limit=5"
```

## Landing page

The marketing site can render a "Community findings" wall by fetching the same
`GET /feed` (CORS is open). Curate it however you like — e.g. show only
`crit`/`warn` severities so the page always looks sharp.

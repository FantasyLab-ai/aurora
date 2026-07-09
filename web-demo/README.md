# Aurora — free hosted demo (`web-demo/`)

A **zero-install, zero-backend** web page that replays Aurora's *real* output on a
few curated datasets: cited findings → an interactive correlation matrix →
click-to-scatter → the do-calculus verdict → the decision. It's the top-of-funnel
fix for "hard to get users": anyone can experience Aurora from a link, with no
download and no signup, then convert to the free desktop app.

Everything is static — `index.html` + `app.js` + `styles.css` + `data/*.json`.
No server, no API keys, no build step.

## What's in `data/`

Pre-computed snapshots generated from real Aurora runs (see `tools/gen` note
below). Each `data/<slug>.json` holds the run's findings, narrative, the
Pearson/Spearman correlation matrices + a downsampled numeric sample (for the
scatter), and a pre-baked causal verdict for the headline pair. `manifest.json`
lists the demos shown in the picker.

To refresh or add datasets: run Aurora on the dataset, then re-run the generator
(`scratchpad/gen_demo_data.py` in the build session) against a local backend and
drop the resulting JSON into `data/`.

## Deploy — Cloudflare Pages (recommended, matches your stack)

No build command, just static assets:

```bash
# one-time
npm i -g wrangler
wrangler pages project create aurora-demo

# deploy the folder
wrangler pages deploy web-demo --project-name aurora-demo
```

That publishes to `https://aurora-demo.pages.dev` (or attach a custom domain like
`demo.fantasylab.ai` in the Cloudflare dashboard → Pages → Custom domains).

Any static host works too (GitHub Pages, Netlify, an R2 bucket behind a Worker) —
there is nothing to run server-side.

## Wire it into the landing page

The landing site lives in the separate `fantasy-labai` project, so add a CTA
that points at the deployed demo. Drop this near your hero:

```html
<a class="cta" href="https://demo.fantasylab.ai" target="_blank" rel="noopener">
  ▶ Try the live demo — no install
</a>
```

Two nice follow-ups once the demo URL is live:
- **Hero button**: "Try it live" next to "Download" — the demo removes the
  unsigned-installer friction from first contact.
- **Embed**: an `<iframe src="https://demo.fantasylab.ai" …>` section titled
  "See it work" so visitors experience the glass box without leaving the page.

## Local preview

```bash
cd web-demo
python -m http.server 8090
# open http://127.0.0.1:8090
```
(Needs a static server rather than `file://` so `fetch("data/…")` resolves.)

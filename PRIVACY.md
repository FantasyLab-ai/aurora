# Aurora — Privacy Policy

_Last updated: 2026-07-11_

Aurora is **local-first by design**. The short version: **your data never leaves
your computer unless you explicitly choose to share a single finding.** There is
no account, no API key, no telemetry, and no analytics.

## What runs where

- **All analysis is on-device.** When you drop a dataset into Aurora, it is read,
  analyzed, and stored **only on your machine**. Your rows, columns, and values
  are never uploaded, and Aurora works fully offline.
- **No telemetry.** Aurora does not track usage, does not phone home, and contains
  no third-party analytics or advertising SDKs.
- **No account.** There is nothing to sign up for and no personal profile.
- **Local storage.** Runs, the knowledge bank, and settings live under
  `~/.aurora/` on your computer. Deleting that folder removes them.

## The only features that use the network — all optional

1. **Knowledge-bank ingest** (optional). To build the citation library, Aurora can
   download **public scientific data** (e.g. FRED, NOAA, NIST, USGS, Gene Ontology)
   to your machine. This is outbound-only to public sources; none of your data is
   sent.
2. **Share a finding to the community** (opt-in, per finding). If you click
   *"Share to community"*, Aurora sends **only**: the finding's title/description,
   the cited method, the **dataset name** (not its contents), and an optional
   confidence value. It **never** sends your raw data, rows, or cell values. Each
   share is a deliberate, one-off action you take.
3. **Sentinel alerts** (only if you configure them). If you set a webhook, Aurora
   posts finding alerts to **your own** Discord/Slack channel. Nothing is sent
   until you provide that webhook.
4. **Updates & downloads.** Downloading the app or checking releases contacts
   GitHub, subject to GitHub's own privacy policy.

## The community feed

Shared findings are stored by a small hosted service (a Cloudflare Worker + KV).
- Stored fields: the finding text, cited method, dataset **name**, severity,
  optional confidence, and a timestamp. **No account, no email, no IP is stored
  with the finding, and no raw data.**
- **Auto-expiry:** shared findings are automatically deleted after **90 days**.
- **Takedown:** to remove a shared finding sooner, contact us (below) and we'll
  delete it.

## Data you control

- **Your datasets & runs:** local only. Delete `~/.aurora/` to remove them.
- **Your shares:** expire in 90 days automatically, or on request.

## Children

Aurora is a general analytics tool and is not directed at children under 13.

## Changes

We'll update this document here in the repository and bump the date above when
anything material changes.

## Contact

Questions or a takedown request: **bgrutkowski13@gmail.com** ·
[github.com/FantasyLab-ai/aurora](https://github.com/FantasyLab-ai/aurora)

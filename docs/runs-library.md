# Aurora Runs Library

The Runs Library replaces the long-standing "Sessions" placeholder in
the Studio with a real per-workspace catalogue of past runs. It ships
in v1.2 and supports three core actions: **pin**, **A/B compare**, and
**share-as-bundle**.

The library is exposed in the Studio top toolbar as the **`runs: N`**
chip. Click for the popover.

## Pinned runs

Pinned runs stay at the top of the list across browser sessions. The
pin list is stored at:

```
<workspace_dir>/runs_library/pinned_runs.json
```

where `<workspace_dir>` is the per-tenant directory under
`$AURORA_DATA_ROOT/workspaces/<id>/` when auth is enabled (Stream 2.4
Phase 2), or `~/.aurora/runs_library/` for single-tenant installs.

- Pin cap: **100 runs** per workspace. The oldest pins drop off when
  you exceed the cap.
- Repinning an already-pinned run refreshes its `pinned_at` and
  optionally updates the note — useful when you want to flag a run
  for fresh attention.
- Unpinning is idempotent (no error if already unpinned).

### Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET`  | `/api/runs/pinned`            | List the workspace's pinned runs |
| `POST` | `/api/runs/pin`               | Body `{"run_id": "...", "note": "..."}` |
| `POST` | `/api/runs/unpin`             | Body `{"run_id": "..."}` |

The annotated `/api/runs` response includes a `pinned: true|false`
field on every entry so the Studio can render the pin marker without
a second fetch.

## A/B compare

Pick exactly two runs in the popover and click **compute**. The Studio
calls:

```
GET /api/runs/compare?run_a_dir=...&run_b_dir=...
```

and renders a side-by-side summary plus a delta block:

```json
{
  "ok": true,
  "run_a_id": "...",   "run_b_id": "...",
  "dataset_a": "...",  "dataset_b": "...",
  "same_dataset": true,
  "summary_a": { "confidence": 0.82, "n_anomalies": 14, ... },
  "summary_b": { "confidence": 0.88, "n_anomalies": 18, ... },
  "delta": {
    "confidence_delta":     0.06,
    "n_anomalies_delta":    4,
    "fabricated_delta":     0,
    "physics_law_changed":  false,
    "hmm_k_delta":          0,
    "same_dataset":         true
  },
  "new_findings":         [...],
  "disappeared_findings": [...],
  "shared_findings_n":    32,
  "join_report":          null,    // populated when datasets differ
  "warnings":             []
}
```

**When the two runs cover the SAME dataset:** the focus is finding-
level diff (what's new since last run, what disappeared, did the
fitted physics law change?).

**When the runs cover DIFFERENT datasets:** the comparator also
embeds the multi-dataset join report (`join_report`) from
`/api/joins/analyze` — surfacing shared keys, schema compatibility,
and inheritance candidates. This is the natural cross-section
between "I ran Aurora on two related datasets; how do they relate?"
and "I re-ran Aurora and want to see what changed."

### Identity-based finding diff

Finding-level diff uses the Stream 1.4 Phase 2 dedupe hash
(`finding_identity`) so a finding gets the same identity in the batch
flow and the streaming flow. If you want to wire your own compare,
import the helper:

```python
from fantasyai.aurora.streaming.dedupe import finding_identity
sig_a = finding_identity(finding_a)
sig_b = finding_identity(finding_b)
```

## Share as bundle

Phase 1 of share-as-bundle (v1.2) is **local file export**: the
Studio extracts the run's Aurora Bundle, writes a portable
`.aurora.json` to a workspace-isolated share directory, and returns
the local path. Any teammate with Aurora installed can verify the
bundle:

```python
import aurora_sdk as aurora
b = aurora.Bundle.load("alice/shared/20260519__demo.aurora.json")
b.verify()      # raises if tampered
```

### Endpoint

```
POST /api/runs/share
{ "run_dir": "...", "note": "..." }
```

Response:

```json
{
  "ok": true,
  "run_id": "...",
  "local_path": "/var/aurora/workspaces/alice/runs_library/shared/r1__1716130800.aurora.json",
  "bundle_hash": "deadbeefcafebabe...",
  "n_bytes": 18432,
  "shared_at": 1716130800.0,
  "note": null,
  "public_url": null     // populated when Aurora Cloud Phase 3 lights up
}
```

### Cloud sharing (Phase 3, planned)

When Aurora Cloud's control plane lights up in Phase 3, `share_run_bundle`
will gain an `upload` mode that mirrors the bundle to a public CDN and
returns a `public_url`. The local-export path stays available either
way — it's the source-of-truth, the cloud copy is a mirror.

## Pin / unpin / share from the SDK

```python
from fantasyai.aurora.runs_library import (
    pin_run, unpin_run, list_pinned_runs,
    compare_runs, share_run_bundle,
)

# Pin a run for the current workspace (or None for single-tenant).
pin_run("20260519_demo", workspace_id="alice", note="baseline")

# Compare two runs.
report = compare_runs("/runs/r1", "/runs/r2")
print(report.delta)

# Export a portable bundle.
info = share_run_bundle("/runs/r1", workspace_id="alice")
print(info.local_path)
```

## What's still placeholder

- The Sessions / Runs library left-rail tile (removed in launch v1.1)
  is not yet restored — the popover chip in the top toolbar covers
  the same use case.
- Public-URL sharing is Phase 3 (depends on cloud control plane).
- "Pin set" — multiple labelled pin groups (e.g., `before-fix` /
  `after-fix`) — is not yet implemented. Today, every pinned run
  shares a single namespace per workspace.

## See also

- [docs/cloud-deploy.md](cloud-deploy.md) — workspace storage layout (where `pinned_runs.json` lives)
- [docs/streaming.md](streaming.md) — the dedupe-hash module the A/B compare reuses
- [ROADMAP.md](../ROADMAP.md) — Phase 3 sharing plans

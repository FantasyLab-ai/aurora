"""
Aurora Runs Library (v1.2-E) — pin, compare, share past runs.

The Studio's old "Sessions" placeholder is finally wired up. This
module backs the RUNS chip + popover in the frontend with:

  * **Pinning** — per-workspace `pinned_runs.json` keeps a small set of
    "important" runs visible at the top of the list. Pinned runs
    survive across sessions; non-pinned older runs scroll off as new
    ones come in.

  * **A/B compare** — given two run_ids, build a comparison report
    (uses the v2.0 multi-dataset joins infrastructure where possible,
    plus per-run summary metrics).

  * **Share-as-bundle** — exports the run's Aurora Bundle and surfaces
    a local path the user can hand off to a teammate. Cloud-hosted
    sharing arrives in Aurora Cloud Phase 3.

The pinned-runs file lives in the workspace storage layout introduced
by Stream 2.4 Phase 2, so it's automatically isolated per tenant when
auth is enabled.
"""
from __future__ import annotations

from .pinned import (
    list_pinned_runs,
    is_pinned,
    pin_run,
    unpin_run,
    pinned_file_path,
)

from .compare import (
    compare_runs,
    RunComparison,
)

from .share import (
    share_run_bundle,
    ShareInfo,
)

__all__ = [
    # Pinning
    "list_pinned_runs",
    "is_pinned",
    "pin_run",
    "unpin_run",
    "pinned_file_path",
    # Compare
    "compare_runs",
    "RunComparison",
    # Share
    "share_run_bundle",
    "ShareInfo",
]

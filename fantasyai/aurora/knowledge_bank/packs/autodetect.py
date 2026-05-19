"""
Auto-pack detection — given an Aurora state dict (or just a domain
hint), figure out which packs should be installed and queue background
downloads.

Design goals:

  1. **Never blocks.** Detection runs synchronously (cheap), download
     spawns a daemon thread. The caller continues immediately.
  2. **Idempotent.** Re-running on the same state does nothing if the
     pack is already installed (or already downloading).
  3. **Opt-out.** Set ``AURORA_NO_PACK_AUTODOWNLOAD=1`` to disable.
  4. **Best-effort.** Any failure (no manifest, network error, sha
     mismatch) logs to stderr and continues — never crashes Aurora.

Call from anywhere downstream of build_state() — e.g., studio_api's
/api/state, or a custom agent's tool wrapper.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .manifest import Manifest, Pack, load_manifest, ManifestError
from .registry import PackRegistry
from .downloader import download_pack, DownloadError
from .installer import install_pack, default_packs_root, InstallError


# Track which packs are currently being downloaded so concurrent
# /api/state calls don't queue the same download twice.
_in_flight: Set[str] = set()
_in_flight_lock = threading.Lock()


def is_autodownload_enabled() -> bool:
    """True unless ``AURORA_NO_PACK_AUTODOWNLOAD`` is set to a truthy value."""
    val = os.environ.get("AURORA_NO_PACK_AUTODOWNLOAD", "").strip().lower()
    return val not in ("1", "true", "yes", "on")


def detect_domains_from_state(state: Dict[str, Any]) -> List[str]:
    """Extract domain hints from an Aurora state dict.

    Looks at multiple signals in priority order:

      1. ``state.system_model.domain`` (string or list)
      2. ``state.system_model.template_id`` (e.g., 'enviro', 'industrial')
      3. Any finding's ``narrative.trace.domain``

    Returns a deduplicated list (preserving first-seen order) of
    lowercase domain strings. Empty list if no signal found.
    """
    if not isinstance(state, dict):
        return []

    out: List[str] = []
    seen: Set[str] = set()

    def _add(val: Any) -> None:
        if not val:
            return
        if isinstance(val, str):
            v = val.strip().lower()
            if v and v not in seen and v != "(none)":
                out.append(v)
                seen.add(v)
        elif isinstance(val, (list, tuple)):
            for x in val:
                _add(x)

    # 1) Explicit system_model fields.
    sm = state.get("system_model") or {}
    if isinstance(sm, dict):
        _add(sm.get("domain"))
        _add(sm.get("template_id"))
        _add(sm.get("template_name"))

    # 2) Finding-level trace.domain (the synthesis layer's view).
    findings = state.get("findings") or []
    if isinstance(findings, list):
        for f in findings:
            if not isinstance(f, dict):
                continue
            narrative = f.get("narrative") or {}
            if isinstance(narrative, dict):
                trace = narrative.get("trace") or {}
                if isinstance(trace, dict):
                    _add(trace.get("domain"))
            # Some finding shapes carry domain at the top level.
            _add(f.get("domain"))

    return out


def pick_packs_for_domains(
    manifest: Manifest,
    domains: List[str],
) -> List[Pack]:
    """Return the manifest packs whose ``covers_domains`` matches any
    of the input domains. Deduplicated by pack id. Preserves manifest
    order so the ordering is deterministic."""
    if not domains:
        return []
    picked: List[Pack] = []
    seen_ids: Set[str] = set()
    for d in domains:
        for p in manifest.for_domain(d):
            if p.id not in seen_ids:
                picked.append(p)
                seen_ids.add(p.id)
    return picked


def maybe_autoinstall(
    state: Dict[str, Any],
    *,
    manifest: Optional[Manifest] = None,
    registry_path: Optional[Path] = None,
    background: bool = True,
) -> List[str]:
    """Examine ``state``, pick relevant packs, and queue downloads for
    any that aren't installed yet.

    Args:
        state:          The state dict from build_state().
        manifest:       Pre-loaded manifest (saves a disk hit on hot path).
                        Defaults to the in-repo canonical manifest.
        registry_path:  Override for the installed-pack registry path
                        (tests use this; production uses the default).
        background:     If True (default), kick the download into a
                        daemon thread and return immediately. If False,
                        block until done (only useful for tests).

    Returns:
        List of pack ids that were queued for download (or installed
        synchronously if ``background=False``). Empty list if nothing
        to do.
    """
    if not is_autodownload_enabled():
        return []

    try:
        m = manifest or load_manifest()
    except ManifestError as e:
        _log(f"manifest load failed: {e}")
        return []

    domains = detect_domains_from_state(state)
    if not domains:
        return []

    candidates = pick_packs_for_domains(m, domains)
    if not candidates:
        return []

    reg = PackRegistry.load(registry_path)

    queued: List[str] = []
    for pack in candidates:
        if reg.is_installed(pack.id, version=pack.version):
            continue  # already have it
        if _claim_in_flight(pack.id):
            queued.append(pack.id)
            if background:
                t = threading.Thread(
                    target=_download_and_install,
                    args=(pack, registry_path),
                    daemon=True,
                    name=f"aurora-pack-{pack.id}",
                )
                t.start()
            else:
                # Synchronous path — tests use this.
                try:
                    _download_and_install(pack, registry_path)
                except Exception:
                    pass

    return queued


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _claim_in_flight(pack_id: str) -> bool:
    """Atomically mark a pack as "currently downloading". Returns True
    if we claimed the slot (caller should download); False if another
    thread is already on it."""
    with _in_flight_lock:
        if pack_id in _in_flight:
            return False
        _in_flight.add(pack_id)
        return True


def _release_in_flight(pack_id: str) -> None:
    with _in_flight_lock:
        _in_flight.discard(pack_id)


def _download_and_install(pack: Pack, registry_path: Optional[Path]) -> None:
    """Top-level worker: download → install → release in-flight slot.
    Errors are caught and logged to stderr. Never raises."""
    try:
        _log(f"downloading pack {pack.id!r} v{pack.version} ({pack.size_mb:.0f} MB)...")
        archive = download_pack(pack)
        _log(f"installing pack {pack.id!r} from {archive.name}...")
        result = install_pack(pack, archive, registry=PackRegistry.load(registry_path))
        _log(f"pack {pack.id!r} ready: {result.entry_count} entries at {result.pack_dir}")
    except (DownloadError, InstallError) as e:
        _log(f"pack {pack.id!r} install failed: {e}")
    except Exception as e:
        _log(f"pack {pack.id!r} unexpected error ({type(e).__name__}): {e}")
    finally:
        _release_in_flight(pack.id)


def _log(msg: str) -> None:
    """Stderr logger. Quiet if AURORA_PACK_QUIET=1."""
    if os.environ.get("AURORA_PACK_QUIET", "").strip() in ("1", "true", "yes"):
        return
    try:
        print(f"[aurora-pack] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass

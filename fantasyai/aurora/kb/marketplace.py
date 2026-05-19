"""
KB pack marketplace listing (v2.0 Stream E).

The pack format + downloader + installer + registry all shipped in
v1.2 (see ``fantasyai/aurora/knowledge_bank/packs/``). v2.0 adds the
**marketplace** layer on top: a single read API that combines the
manifest catalogue with the install-state of each pack on this
machine, ready for a frontend panel to render.

We don't add a server-side marketplace right now — the manifest is
already a curated, signed-ish JSON file in the repo. "Listing" =
read manifest + cross-reference local installed-pack registry.

When Aurora Cloud Phase 3 lights up a real marketplace control plane
(submission flow, review, revenue share), this module gains an
optional ``remote_manifest_url`` mode. Local-first stays the
default.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class MarketplaceEntry:
    """One pack as seen from the marketplace."""
    id:               str
    name:             str
    description:      str
    domain_tags:      List[str]
    method_coverage:  List[str]
    version:          int
    size_mb:          float
    entry_count:      int
    license:          str
    maintainer:       Optional[str] = None
    homepage:         Optional[str] = None
    url_primary:      Optional[str] = None
    url_mirror:       Optional[str] = None
    sha256:           Optional[str] = None
    installed:        bool = False
    installed_version: Optional[int] = None
    installed_at:     Optional[float] = None
    install_state:    str = "available"   # available | installed | upgrade_available | failed

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_manifest() -> Dict[str, Any]:
    """Read the bundled manifest. Tolerates missing file."""
    try:
        from fantasyai.aurora.knowledge_bank.packs import manifest as manifest_mod
        path = getattr(manifest_mod, "__file__", None)
        if path:
            json_path = Path(path).parent / "manifest.json"
            if json_path.exists():
                return json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"schema_version": "1.0.0", "packs": []}


def _installed_packs(workspace_id: Optional[str] = None
                     ) -> Dict[str, Dict[str, Any]]:
    """Map pack_id → install record. Looks up the existing registry
    from ``fantasyai.aurora.knowledge_bank.packs.registry``."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from fantasyai.aurora.knowledge_bank.packs.registry import (
            list_installed_packs,
        )
        for rec in list_installed_packs() or []:
            pid = (rec or {}).get("id") or (rec or {}).get("pack_id")
            if pid:
                out[str(pid)] = rec
    except Exception:
        pass
    # Also consider workspace-installed packs (Phase 2 isolation).
    try:
        from fantasyai.aurora.auth import workspace_dir
        ws = workspace_dir(workspace_id)
        ws_registry = ws / "kb" / "installed_packs.json"
        if ws_registry.exists():
            data = json.loads(ws_registry.read_text(encoding="utf-8"))
            for rec in (data.get("packs") or []):
                pid = (rec or {}).get("id")
                if pid:
                    out[str(pid)] = rec
    except Exception:
        pass
    return out


def list_marketplace(workspace_id: Optional[str] = None
                      ) -> List[Dict[str, Any]]:
    """Return one entry per pack in the manifest, annotated with
    install state for the current workspace.
    """
    manifest = _load_manifest()
    installed = _installed_packs(workspace_id)
    out: List[Dict[str, Any]] = []
    for raw in manifest.get("packs") or []:
        if not isinstance(raw, dict):
            continue
        pid = raw.get("id")
        if not pid:
            continue
        inst = installed.get(pid) or {}
        installed_ver = inst.get("version") or inst.get("installed_version")
        installed_at = inst.get("installed_at")
        is_installed = bool(inst)
        # Manifest entries whose sha256 is "PENDING_FIRST_BUILD" (or
        # missing) are reservation slots — listed for transparency but
        # not actually hosted yet. Mark them clearly so the UI can
        # render a "preview" badge + disabled install button.
        sha = str(raw.get("sha256") or "")
        is_preview = sha.upper().startswith("PENDING") or not sha
        state = "available"
        if is_preview:
            state = "preview"
        if is_installed:
            try:
                if installed_ver is not None and raw.get("version") is not None \
                        and int(installed_ver) < int(raw["version"]):
                    state = "upgrade_available"
                else:
                    state = "installed"
            except Exception:
                state = "installed"
        entry = MarketplaceEntry(
            id=str(pid),
            name=str(raw.get("name") or pid),
            description=str(raw.get("description") or ""),
            domain_tags=list(raw.get("covers_domains") or []),
            method_coverage=list(raw.get("covers_methods") or []),
            version=int(raw.get("version") or 1),
            size_mb=float(raw.get("size_mb") or 0),
            entry_count=int(raw.get("entry_count") or 0),
            license=str(raw.get("license") or ""),
            maintainer=raw.get("maintainer"),
            homepage=raw.get("homepage"),
            url_primary=raw.get("url_primary"),
            url_mirror=raw.get("url_mirror"),
            sha256=raw.get("sha256"),
            installed=is_installed,
            installed_version=int(installed_ver) if installed_ver else None,
            installed_at=float(installed_at) if installed_at else None,
            install_state=state,
        )
        out.append(entry.to_dict())
    return out


def _resolve_pack_from_manifest(pack_id: str):
    """Find a Pack object in the bundled manifest by id."""
    from fantasyai.aurora.knowledge_bank.packs.manifest import load_manifest
    manifest = load_manifest()
    for p in (manifest.packs or []):
        if str(getattr(p, "id", None)) == str(pack_id):
            return p
    return None


def install_pack(
    pack_id: str,
    *,
    workspace_id: Optional[str] = None,
    use_mirror: bool = False,
) -> Dict[str, Any]:
    """Install a pack from the manifest. Returns a status dict.

    Args:
        pack_id: the manifest id (e.g. "finance", "industrial").
        workspace_id: reserved for Phase 2 cloud / workspace-scoped
            installs; today we install to the shared on-disk location.
        use_mirror: prefer the HuggingFace mirror over the Cloudflare R2
            primary. Useful when the primary CDN is rate-limiting.

    The full flow:
      1. Find the Pack in the bundled manifest.
      2. Refuse early if the pack has SHA=PENDING_FIRST_BUILD — those
         packs are listed in the manifest as a roadmap signal but
         aren't actually built / hosted yet.
      3. Download via ``downloader.download_pack``.
      4. Install via ``installer.install_pack``.
      5. Surface a structured result dict so the frontend can render
         either success ("installed v1 — 1240 entries") or an
         actionable error.
    """
    pack = _resolve_pack_from_manifest(pack_id)
    if pack is None:
        return {"ok": False, "pack_id": pack_id,
                "error": f"pack {pack_id!r} not in manifest"}

    sha = str(getattr(pack, "sha256", "") or "")
    if sha.upper().startswith("PENDING") or not sha:
        # Honest signal: these manifest entries are reserved slots,
        # not yet built / hosted. Frontend renders them as "preview"
        # and the install button should be disabled. We still return
        # a clean error if someone POSTs anyway.
        return {"ok": False, "pack_id": pack_id,
                "preview": True,
                "error": (f"pack {pack_id!r} is reserved in the manifest but "
                           f"not yet released (sha256={sha or '<none>'}). "
                           f"Wait for the next manifest revision."),
                "manifest_revision": pack_id}

    try:
        from fantasyai.aurora.knowledge_bank.packs.downloader import (
            download_pack, DownloadError,
        )
        from fantasyai.aurora.knowledge_bank.packs.installer import (
            install_pack as _install_pack, InstallError,
        )
    except Exception as e:
        return {"ok": False, "pack_id": pack_id,
                "error": f"pack install pipeline unavailable: "
                          f"{type(e).__name__}: {e}"}

    # Optionally flip primary / mirror order if the caller asked.
    if use_mirror:
        try:
            pack.url_primary, pack.url_mirror = pack.url_mirror, pack.url_primary
        except Exception:
            pass

    try:
        archive_path = download_pack(pack)
    except Exception as e:
        return {"ok": False, "pack_id": pack_id,
                "stage": "download",
                "error": f"download failed: {type(e).__name__}: {e}"}

    try:
        result = _install_pack(pack, archive_path)
    except Exception as e:
        return {"ok": False, "pack_id": pack_id,
                "stage": "install",
                "error": f"install failed: {type(e).__name__}: {e}"}

    return {
        "ok":          True,
        "pack_id":     getattr(result, "pack_id", pack_id),
        "version":     getattr(result, "version", None),
        "pack_dir":    str(getattr(result, "pack_dir", "")),
        "entry_count": getattr(result, "entry_count", None),
    }


def uninstall_pack(
    pack_id: str,
    *,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Uninstall a previously-installed pack. The shared registry
    handles disk-level removal; a workspace-installed pack is
    removed from the workspace's installed_packs.json instead."""
    # Try the shared installer first.
    try:
        from fantasyai.aurora.knowledge_bank.packs.registry import (
            unregister_pack,
        )
    except Exception:
        unregister_pack = None  # type: ignore
    removed_shared = False
    if unregister_pack is not None:
        try:
            removed_shared = bool(unregister_pack(pack_id))
        except Exception:
            removed_shared = False
    # Workspace-scoped removal (best-effort).
    removed_ws = False
    try:
        from fantasyai.aurora.auth import workspace_dir
        ws = workspace_dir(workspace_id)
        wf = ws / "kb" / "installed_packs.json"
        if wf.exists():
            data = json.loads(wf.read_text(encoding="utf-8"))
            old = data.get("packs") or []
            new = [p for p in old if str(p.get("id")) != str(pack_id)]
            if len(new) != len(old):
                data["packs"] = new
                wf.write_text(json.dumps(data, indent=2, default=str),
                                encoding="utf-8")
                removed_ws = True
    except Exception:
        pass
    return {
        "ok":      removed_shared or removed_ws,
        "pack_id": pack_id,
        "removed_shared": removed_shared,
        "removed_workspace": removed_ws,
    }

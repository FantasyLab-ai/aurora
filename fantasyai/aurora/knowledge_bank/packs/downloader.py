"""
Pack downloader — fetch a pack tarball over HTTP with sha256 verification
and primary/mirror failover.

Why we don't use ``requests``: Aurora's runtime deps stay deliberately
minimal (see requirements.txt). ``urllib`` is in the stdlib and is
sufficient for "download a file and check its hash." No streaming retry
logic beyond mirror failover — if both URLs fail, the caller decides
what to do.

Why we verify sha256 before installing: the manifest is the authority.
Even if someone compromises one of the CDNs, mismatched bytes are
rejected. We never extract before verification.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

from .manifest import Pack


# Per-request timeout (seconds). Pack archives are typically 50-500 MB,
# so we need a generous timeout but not infinite.
DEFAULT_TIMEOUT_S = 300.0

# When the user has a slow connection, urllib's blocking read() can stall
# silently. We chunk the read and report progress every N bytes so the
# user sees "still downloading" instead of suspecting a hang.
CHUNK_BYTES = 1 << 17  # 128 KB

# Cap pack size at 2 GB. If a manifest ever lists something bigger,
# something is wrong — we refuse rather than fill the user's disk.
MAX_PACK_BYTES = 2 * 1024 * 1024 * 1024


class DownloadError(Exception):
    """Raised when a pack cannot be downloaded or fails verification.

    Always carries a message describing exactly which URL was tried and
    what went wrong, so users can debug without source diving."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def download_pack(
    pack: Pack,
    *,
    dest_dir: Optional[Path] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Download a pack to a local path and return that path.

    Tries ``pack.url_primary`` first, falls back to ``pack.url_mirror``
    on any failure. Verifies sha256 against ``pack.sha256`` before
    returning — sha mismatch deletes the file and raises.

    Args:
        pack:      The Pack to fetch.
        dest_dir:  Where to write the .tar.gz. Defaults to a temp dir.
                   Caller is responsible for cleanup if they want it.
        timeout:   Per-request HTTP timeout in seconds.
        progress:  Optional callback ``(bytes_so_far, total_bytes)``.
                   Called every CHUNK_BYTES during download. ``total``
                   is the server-reported Content-Length or 0 if unknown.

    Returns:
        Path to the downloaded, sha-verified .tar.gz file.

    Raises:
        DownloadError: both URLs failed, sha mismatch, or sanity check.
    """
    if dest_dir is None:
        dest_dir = Path(tempfile.mkdtemp(prefix="aurora_pack_"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    target = dest_dir / pack.archive_name

    # Pre-flight: pack must specify at least one URL and a sha256.
    if not (pack.url_primary or pack.url_mirror):
        raise DownloadError(
            f"pack {pack.id!r} has no url_primary or url_mirror set; "
            f"manifest is incomplete"
        )
    if not pack.sha256 or len(pack.sha256) < 32:
        raise DownloadError(
            f"pack {pack.id!r} has no sha256 in manifest; refusing to download "
            f"without an integrity check"
        )

    # Try primary first, then mirror.
    urls = [u for u in (pack.url_primary, pack.url_mirror) if u]
    last_err: Optional[str] = None
    for i, url in enumerate(urls):
        label = "primary" if i == 0 else "mirror"
        try:
            _fetch_to(url, target, timeout=timeout, progress=progress, label=label)
            actual_sha = _sha256(target)
            if actual_sha != pack.sha256:
                # Mismatch — delete the bad file and try next URL.
                try:
                    target.unlink()
                except OSError:
                    pass
                last_err = (
                    f"{label} URL returned content with sha256 {actual_sha[:16]}…, "
                    f"expected {pack.sha256[:16]}…"
                )
                continue
            return target
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = f"{label} URL {url} failed: {type(e).__name__}: {e}"
            continue

    raise DownloadError(
        f"could not download pack {pack.id!r} from any URL. "
        f"Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _fetch_to(
    url: str,
    target: Path,
    *,
    timeout: float,
    progress: Optional[Callable[[int, int], None]],
    label: str,
) -> None:
    """Stream the URL into ``target``, chunked. Raises on network error
    or oversized response (MAX_PACK_BYTES)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"aurora-kb-pack-downloader/1.0",
            "Accept": "application/gzip, application/octet-stream",
        },
    )
    # urlopen raises URLError / HTTPError on network failures, which the
    # caller catches and rolls over to the mirror.
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = 0
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        if total > MAX_PACK_BYTES:
            raise DownloadError(
                f"{label} URL announced {total} bytes, exceeds {MAX_PACK_BYTES} "
                f"safety cap; refusing to download"
            )
        # Write to a sibling .part file, rename on success — keeps the
        # caller from seeing a truncated target if we crash mid-write.
        part = target.with_suffix(target.suffix + ".part")
        try:
            with open(part, "wb") as f:
                seen = 0
                while True:
                    chunk = resp.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    seen += len(chunk)
                    if seen > MAX_PACK_BYTES:
                        raise DownloadError(
                            f"{label} URL exceeded {MAX_PACK_BYTES}-byte safety "
                            f"cap mid-stream; aborting"
                        )
                    f.write(chunk)
                    if progress is not None:
                        try:
                            progress(seen, total)
                        except Exception:
                            pass  # never let a callback break the download
            os.replace(str(part), str(target))
        finally:
            # If we errored out, clean up the partial file.
            if part.exists() and not target.exists():
                try:
                    part.unlink()
                except OSError:
                    pass


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    """Compute the sha256 of a file. Streamed so we don't materialise
    the entire file in memory (packs can be hundreds of MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Stderr progress helper (default for CLI / Studio)
# ---------------------------------------------------------------------------

def stderr_progress(seen: int, total: int) -> None:
    """A default progress callback that prints to stderr.

    Format: ``[aurora-kb-pack] 145.2 MB / 200.0 MB (72%)``. Uses a CR
    so successive updates overwrite the same line in a real terminal,
    and a final newline once complete."""
    mb_seen = seen / (1024 * 1024)
    if total:
        mb_total = total / (1024 * 1024)
        pct = (seen * 100) // total
        msg = f"\r[aurora-kb-pack] {mb_seen:7.1f} MB / {mb_total:7.1f} MB ({pct:3d}%)"
    else:
        msg = f"\r[aurora-kb-pack] {mb_seen:7.1f} MB (size unknown)"
    sys.stderr.write(msg)
    sys.stderr.flush()
    if total and seen >= total:
        sys.stderr.write("\n")
        sys.stderr.flush()

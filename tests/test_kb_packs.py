"""
Tests for the knowledge-pack distribution layer.

These tests are deliberately *hermetic* — no real network calls, no
real downloads, no dependence on the maintainer's environment. We:

  * Use the in-tree manifest.json for shape tests
  * Build a synthetic tar.gz on disk and serve it via a stub urlopen
  * Use a tmp_path for the install dir
  * Compute the sha256 of our synthetic archive and feed it into the
    Pack we test against

The goal: every test runnable on CI for every Python version, no flake.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.knowledge_bank.packs import (
    Pack,
    Manifest,
    ManifestError,
    PackSchemaError,
    load_manifest,
    load_manifest_from_str,
    DEFAULT_MANIFEST_PATH,
    InstalledPack,
    PackRegistry,
    DownloadError,
    download_pack,
    InstallError,
    install_pack,
)
from fantasyai.aurora.knowledge_bank.packs.installer import default_packs_root


# ---------------------------------------------------------------------------
# Manifest shape tests
# ---------------------------------------------------------------------------

class TestManifestLoading:

    def test_in_tree_manifest_loads_and_validates(self):
        """The manifest committed to the repo must always be valid."""
        m = load_manifest()
        assert isinstance(m, Manifest)
        assert m.schema_version.startswith("1.")
        # We ship 4 starter packs.
        assert len(m.packs) >= 4
        # IDs must be unique.
        ids = [p.id for p in m.packs]
        assert len(ids) == len(set(ids))
        # The 4 launch domains must be represented.
        for needed in ("finance", "industrial", "climate", "biomedical"):
            assert m.get(needed) is not None, f"missing pack id {needed!r}"

    def test_load_manifest_from_str_round_trips(self):
        m = load_manifest()
        s = json.dumps(m.to_dict())
        m2 = load_manifest_from_str(s)
        assert m2.ids() == m.ids()

    def test_load_manifest_rejects_bad_json(self):
        with pytest.raises(ManifestError):
            load_manifest_from_str("not json {{{")

    def test_load_manifest_rejects_wrong_schema_version(self):
        bad = json.dumps({"schema_version": "2.0.0", "packs": []})
        with pytest.raises(ManifestError, match="schema_version"):
            load_manifest_from_str(bad)

    def test_load_manifest_rejects_non_dict_root(self):
        with pytest.raises(ManifestError):
            load_manifest_from_str("[]")

    def test_load_manifest_rejects_duplicate_pack_id(self):
        m = load_manifest()
        # Take one pack and duplicate it.
        d = m.to_dict()
        d["packs"].append(d["packs"][0])
        with pytest.raises(ManifestError, match="duplicate"):
            load_manifest_from_str(json.dumps(d))

    def test_pack_from_dict_requires_essential_fields(self):
        # Missing 'sha256' — must blow up cleanly.
        with pytest.raises(PackSchemaError, match="missing required"):
            Pack.from_dict({"id": "x"})


class TestManifestQueries:

    def test_for_domain_finds_finance_pack(self):
        m = load_manifest()
        finance = m.for_domain("finance")
        assert len(finance) >= 1
        assert any(p.id == "finance" for p in finance)

    def test_for_domain_case_insensitive(self):
        m = load_manifest()
        assert len(m.for_domain("FINANCE")) >= 1
        assert len(m.for_domain("Finance")) >= 1

    def test_for_method_routes_correctly(self):
        m = load_manifest()
        # 'granger' is declared by the finance pack at minimum.
        packs = m.for_method("granger")
        assert any(p.id == "finance" for p in packs)

    def test_unknown_domain_returns_empty(self):
        m = load_manifest()
        assert m.for_domain("nonexistent_domain_xyz") == []


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestPackRegistry:

    def test_empty_registry_when_file_missing(self, tmp_path):
        path = tmp_path / "_installed.json"
        reg = PackRegistry.load(path)
        assert reg.installed_ids() == []
        assert not reg.is_installed("finance")

    def test_mark_installed_and_save_roundtrip(self, tmp_path):
        path = tmp_path / "_installed.json"
        reg = PackRegistry.load(path)
        reg.mark_installed(InstalledPack(
            id="finance",
            version=1,
            installed_at="2026-06-01T12:00:00+00:00",
            sha256="a" * 64,
            entry_count=8420,
            pack_dir=str(tmp_path / "finance-v1"),
        ))
        reg.save()

        reg2 = PackRegistry.load(path)
        assert reg2.is_installed("finance")
        assert reg2.is_installed("finance", version=1)
        assert not reg2.is_installed("finance", version=2)
        assert reg2.get("finance").entry_count == 8420

    def test_remove(self, tmp_path):
        path = tmp_path / "_installed.json"
        reg = PackRegistry.load(path)
        reg.mark_installed(InstalledPack(
            id="x", version=1, installed_at="t",
            sha256="b" * 64, entry_count=1, pack_dir="/tmp/x",
        ))
        assert reg.remove("x") is True
        assert reg.remove("x") is False  # already gone
        assert not reg.is_installed("x")

    def test_corrupt_registry_treated_as_empty(self, tmp_path):
        path = tmp_path / "_installed.json"
        path.write_text("not json {{{", encoding="utf-8")
        reg = PackRegistry.load(path)
        assert reg.installed_ids() == []


# ---------------------------------------------------------------------------
# Downloader tests (stub HTTP via monkeypatch)
# ---------------------------------------------------------------------------

class _StubResponse:
    """Quacks like the object urllib.request.urlopen returns."""

    def __init__(self, body: bytes, content_length: int):
        self._body = body
        self._read_offset = 0
        self.headers = {"Content-Length": str(content_length)}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            buf = self._body[self._read_offset:]
            self._read_offset = len(self._body)
            return buf
        buf = self._body[self._read_offset:self._read_offset + size]
        self._read_offset += len(buf)
        return buf

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_synthetic_pack_tarball(
    pack_id: str,
    version: int,
    embedder: str = "all-MiniLM-L6-v2",
    embedding_dim: int = 384,
    entry_count: int = 3,
) -> bytes:
    """Build a valid pack tarball in memory and return its bytes.

    Useful for testing both downloader (serves it via stub) and
    installer (writes it to disk then installs).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        # pack_meta.json
        meta = {
            "id": pack_id,
            "version": version,
            "entry_count": entry_count,
            "embedder": embedder,
            "embedding_dim": embedding_dim,
            "schema_version": "1.0.0",
        }
        meta_bytes = json.dumps(meta).encode("utf-8")
        info = tarfile.TarInfo("pack_meta.json")
        info.size = len(meta_bytes)
        tf.addfile(info, io.BytesIO(meta_bytes))
        # entries.jsonl — 3 dummy entries
        entries = "\n".join(
            json.dumps({"id": f"seed:test_{i}", "name": f"Entry {i}"})
            for i in range(entry_count)
        ) + "\n"
        entries_bytes = entries.encode("utf-8")
        info = tarfile.TarInfo("entries.jsonl")
        info.size = len(entries_bytes)
        tf.addfile(info, io.BytesIO(entries_bytes))
        # embeddings.npy — a 1-byte placeholder is fine for shape tests
        # (we don't validate npy format in tests, only that the file exists)
        emb_bytes = b"\x93NUMPY"  # NPY magic, enough to be non-empty
        info = tarfile.TarInfo("embeddings.npy")
        info.size = len(emb_bytes)
        tf.addfile(info, io.BytesIO(emb_bytes))
    return buf.getvalue()


def _make_pack(
    *, pack_id="finance", version=1, sha256: str,
    url_primary="https://example.test/finance-v1.tar.gz",
    url_mirror="https://mirror.test/finance-v1.tar.gz",
) -> Pack:
    return Pack(
        id=pack_id, name="t", description="t",
        version=version, size_mb=0.1, entry_count=3,
        embedder="all-MiniLM-L6-v2", embedding_dim=384,
        url_primary=url_primary, url_mirror=url_mirror,
        sha256=sha256,
    )


class TestDownloader:

    def test_downloads_and_verifies(self, tmp_path, monkeypatch):
        body = _make_synthetic_pack_tarball("finance", 1)
        good_sha = hashlib.sha256(body).hexdigest()
        pack = _make_pack(sha256=good_sha)

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return _StubResponse(body, len(body))

        monkeypatch.setattr(
            "fantasyai.aurora.knowledge_bank.packs.downloader.urllib.request.urlopen",
            fake_urlopen,
        )

        out = download_pack(pack, dest_dir=tmp_path)
        assert out.exists()
        assert out.read_bytes() == body
        # Only primary URL hit, mirror unused.
        assert len(calls) == 1
        assert calls[0] == pack.url_primary

    def test_fails_over_to_mirror_on_primary_404(self, tmp_path, monkeypatch):
        body = _make_synthetic_pack_tarball("finance", 1)
        good_sha = hashlib.sha256(body).hexdigest()
        pack = _make_pack(sha256=good_sha)

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if req.full_url == pack.url_primary:
                import urllib.error
                raise urllib.error.HTTPError(
                    req.full_url, 404, "Not Found", {}, None,
                )
            return _StubResponse(body, len(body))

        monkeypatch.setattr(
            "fantasyai.aurora.knowledge_bank.packs.downloader.urllib.request.urlopen",
            fake_urlopen,
        )

        out = download_pack(pack, dest_dir=tmp_path)
        assert out.exists()
        assert calls == [pack.url_primary, pack.url_mirror]

    def test_rejects_sha256_mismatch(self, tmp_path, monkeypatch):
        body = _make_synthetic_pack_tarball("finance", 1)
        wrong_sha = "0" * 64
        pack = _make_pack(sha256=wrong_sha)

        def fake_urlopen(req, timeout=None):
            return _StubResponse(body, len(body))

        monkeypatch.setattr(
            "fantasyai.aurora.knowledge_bank.packs.downloader.urllib.request.urlopen",
            fake_urlopen,
        )

        with pytest.raises(DownloadError, match="could not download"):
            download_pack(pack, dest_dir=tmp_path)

    def test_refuses_without_sha256(self, tmp_path):
        pack = _make_pack(sha256="")  # empty sha
        with pytest.raises(DownloadError, match="no sha256"):
            download_pack(pack, dest_dir=tmp_path)

    def test_refuses_without_urls(self, tmp_path):
        pack = _make_pack(
            sha256="a" * 64,
            url_primary="",
            url_mirror="",
        )
        with pytest.raises(DownloadError, match="no url_primary or url_mirror"):
            download_pack(pack, dest_dir=tmp_path)


# ---------------------------------------------------------------------------
# Installer tests
# ---------------------------------------------------------------------------

class TestInstaller:

    def test_install_extracts_and_registers(self, tmp_path):
        body = _make_synthetic_pack_tarball("finance", 1)
        sha = hashlib.sha256(body).hexdigest()
        archive = tmp_path / "finance-v1.tar.gz"
        archive.write_bytes(body)

        pack = _make_pack(sha256=sha)
        reg_path = tmp_path / "_installed.json"
        reg = PackRegistry.load(reg_path)
        packs_root = tmp_path / "packs"

        result = install_pack(
            pack, archive,
            packs_root=packs_root,
            registry=reg,
        )

        # Files extracted to expected location.
        assert result.pack_dir == packs_root / "finance-v1"
        assert (result.pack_dir / "pack_meta.json").exists()
        assert (result.pack_dir / "entries.jsonl").exists()
        assert (result.pack_dir / "embeddings.npy").exists()
        # Registry updated.
        assert reg.is_installed("finance")
        # Entry count read correctly (3 in our synthetic pack).
        assert reg.get("finance").entry_count == 3

    def test_install_refuses_mismatched_pack_id_in_meta(self, tmp_path):
        # Pack tarball claims to be 'industrial', manifest says 'finance'.
        body = _make_synthetic_pack_tarball("industrial", 1)
        sha = hashlib.sha256(body).hexdigest()
        archive = tmp_path / "finance-v1.tar.gz"
        archive.write_bytes(body)
        pack = _make_pack(pack_id="finance", sha256=sha)

        with pytest.raises(InstallError, match="disagrees with manifest id"):
            install_pack(pack, archive, packs_root=tmp_path / "packs")

    def test_install_refuses_mismatched_embedder(self, tmp_path):
        body = _make_synthetic_pack_tarball(
            "finance", 1, embedder="different-embedder",
        )
        sha = hashlib.sha256(body).hexdigest()
        archive = tmp_path / "finance-v1.tar.gz"
        archive.write_bytes(body)
        pack = _make_pack(sha256=sha)  # manifest says all-MiniLM-L6-v2

        with pytest.raises(InstallError, match="embedder"):
            install_pack(pack, archive, packs_root=tmp_path / "packs")

    def test_install_refuses_path_traversal_in_archive(self, tmp_path):
        # Craft a malicious tarball that tries to escape extraction dir.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("../../etc/passwd")
            data = b"oh no"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        body = buf.getvalue()
        sha = hashlib.sha256(body).hexdigest()
        archive = tmp_path / "evil.tar.gz"
        archive.write_bytes(body)
        pack = _make_pack(sha256=sha)

        with pytest.raises(InstallError, match="escape"):
            install_pack(pack, archive, packs_root=tmp_path / "packs")

    def test_install_refuses_unexpected_files_in_archive(self, tmp_path):
        # Pack contains an unexpected file.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in ("pack_meta.json", "entries.jsonl", "embeddings.npy", "evil.sh"):
                data = b"x" if name == "evil.sh" else b'{"id": "x"}'
                if name == "pack_meta.json":
                    data = json.dumps({
                        "id": "finance", "version": 1, "entry_count": 0,
                        "embedder": "all-MiniLM-L6-v2", "embedding_dim": 384,
                    }).encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        body = buf.getvalue()
        sha = hashlib.sha256(body).hexdigest()
        archive = tmp_path / "evil.tar.gz"
        archive.write_bytes(body)
        pack = _make_pack(sha256=sha)

        with pytest.raises(InstallError, match="unexpected file"):
            install_pack(pack, archive, packs_root=tmp_path / "packs")

"""
Q2.0 Streams C/D/E/F tests:
    * KB ingestion (PDF + folder + storage)
    * Bundle attestation (verify + trusted-signer registry)
    * KB marketplace listing
    * GPU-embeddings device selection
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest


# ----------------------------------------------------------------------
# Stream C: KB ingestion
# ----------------------------------------------------------------------

class TestPDFTextChunking:

    def test_chunk_short_text_returns_single(self):
        from fantasyai.aurora.kb.ingest.pdf_ingester import _chunk_long_text
        chunks = _chunk_long_text("hello world",
                                    source_path="x", source_hash="h",
                                    page_number=1, max_chars=200)
        assert len(chunks) == 1
        assert chunks[0].text == "hello world"
        assert chunks[0].source_hash == "h"
        assert chunks[0].section_index == 0

    def test_chunk_long_text_splits_at_paragraphs(self):
        from fantasyai.aurora.kb.ingest.pdf_ingester import _chunk_long_text
        text = ("para1 " * 100) + "\n\n" + ("para2 " * 100) + "\n\n" + ("para3 " * 100)
        chunks = _chunk_long_text(text, source_path="x",
                                    source_hash="h", page_number=1,
                                    max_chars=700)
        assert len(chunks) >= 2
        # Section indexes are sequential.
        for i, c in enumerate(chunks):
            assert c.section_index == i

    def test_extract_falls_back_to_text_neighbour(self, tmp_path):
        from fantasyai.aurora.kb.ingest import extract_text_from_pdf
        # Create a "PDF" path with a sibling .txt — pypdf will fail to
        # parse it as a PDF and we fall back to the text neighbour.
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"not really a pdf")
        txt = tmp_path / "paper.txt"
        txt.write_text("Body of the paper here. Section 2 follows.",
                        encoding="utf-8")
        chunks = extract_text_from_pdf(pdf)
        assert len(chunks) == 1
        assert "Body of the paper" in chunks[0].text
        assert chunks[0].source_path.endswith("paper.txt")

    def test_extract_raises_when_no_text_available(self, tmp_path):
        from fantasyai.aurora.kb.ingest import (
            extract_text_from_pdf, PDFIngestError,
        )
        pdf = tmp_path / "bad.pdf"
        pdf.write_bytes(b"not a pdf")
        with pytest.raises(PDFIngestError):
            extract_text_from_pdf(pdf)


class TestFolderIngestion:

    def test_ingest_folder_walks_text_files(self, monkeypatch, tmp_path):
        from fantasyai.aurora.kb.ingest import ingest_folder
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path / "data"))
        # Build a small tree with two .md files and one unsupported .zip.
        src = tmp_path / "papers"
        src.mkdir()
        (src / "a.md").write_text("Paper A content here.")
        (src / "b.txt").write_text("Paper B content here. More text.")
        (src / "weird.zip").write_bytes(b"\x00\x01")
        report = ingest_folder(src, workspace_id="alice")
        assert report.ok is True
        assert report.n_ingested == 2
        assert report.n_skipped == 1
        # The output file lives under the workspace.
        assert "alice" in (report.out_path or "")

    def test_ingest_folder_idempotent(self, monkeypatch, tmp_path):
        """Re-ingesting the same folder twice produces stable entries."""
        from fantasyai.aurora.kb.ingest import (
            ingest_folder, list_workspace_kb_entries,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path / "data"))
        src = tmp_path / "papers"
        src.mkdir()
        (src / "a.md").write_text("alpha")
        (src / "b.md").write_text("beta")
        ingest_folder(src, workspace_id="alice", append=False)
        n1 = len(list_workspace_kb_entries(workspace_id="alice"))
        ingest_folder(src, workspace_id="alice", append=True)
        n2 = len(list_workspace_kb_entries(workspace_id="alice"))
        assert n1 == n2  # deduped on seed_id

    def test_ingest_folder_workspace_isolation(self, monkeypatch, tmp_path):
        from fantasyai.aurora.kb.ingest import (
            ingest_folder, list_workspace_kb_entries,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path / "data"))
        src_a = tmp_path / "papers_a"
        src_a.mkdir()
        (src_a / "x.md").write_text("alice paper")
        src_b = tmp_path / "papers_b"
        src_b.mkdir()
        (src_b / "y.md").write_text("bob paper")
        ingest_folder(src_a, workspace_id="alice")
        ingest_folder(src_b, workspace_id="bob")
        a_entries = list_workspace_kb_entries(workspace_id="alice")
        b_entries = list_workspace_kb_entries(workspace_id="bob")
        assert any("alice" in e["content"] for e in a_entries)
        assert any("bob" in e["content"] for e in b_entries)
        # Cross-tenant invisibility.
        assert not any("bob" in e["content"] for e in a_entries)


# ----------------------------------------------------------------------
# Stream D: Attestation
# ----------------------------------------------------------------------

class TestTrustedSignerRegistry:

    def _valid_pk(self):
        # 32 bytes → 64 hex chars.
        return "a" * 64

    def test_add_then_list(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import (
            add_trusted_signer, list_trusted_signers,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        add_trusted_signer(self._valid_pk(), workspace_id="alice",
                            label="alice's key",
                            domains=["climate"])
        signers = list_trusted_signers("alice")
        assert len(signers) == 1
        assert signers[0]["label"] == "alice's key"
        assert "climate" in signers[0]["domains"]

    def test_add_rejects_bad_hex(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import add_trusted_signer
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        with pytest.raises(ValueError):
            add_trusted_signer("notlongenough", workspace_id="alice")
        with pytest.raises(ValueError):
            add_trusted_signer("z" * 64, workspace_id="alice")

    def test_add_idempotent(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import (
            add_trusted_signer, list_trusted_signers,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        add_trusted_signer(self._valid_pk(), workspace_id="alice",
                            label="first")
        add_trusted_signer(self._valid_pk(), workspace_id="alice",
                            label="second")
        signers = list_trusted_signers("alice")
        assert len(signers) == 1
        assert signers[0]["label"] == "second"

    def test_remove(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import (
            add_trusted_signer, remove_trusted_signer, list_trusted_signers,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        add_trusted_signer(self._valid_pk(), workspace_id="alice")
        assert remove_trusted_signer(self._valid_pk(),
                                       workspace_id="alice") is True
        assert list_trusted_signers("alice") == []

    def test_workspace_isolation(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import (
            add_trusted_signer, list_trusted_signers,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        add_trusted_signer(self._valid_pk(), workspace_id="alice")
        assert list_trusted_signers("alice")
        assert list_trusted_signers("bob") == []


class TestAttestBundle:

    def _good_bundle(self):
        """Build a minimal bundle whose content hash actually verifies."""
        from aurora_sdk import bundle as sdk_bundle
        doc = {
            "schema_version": "1.0",
            "generated_at": "2026-01-01T00:00:00Z",
            "run": {"run_id": "r1"},
            "dataset": {"name": "demo.csv", "sha256": "abc"},
            "findings": [],
        }
        h = sdk_bundle._compute_content_hash(doc)
        doc["integrity"] = {"content_hash": h}
        return doc

    def test_attest_signed_unknown_signer_returns_untrusted(self, monkeypatch, tmp_path):
        """A bundle that passes integrity but whose signer isn't in the
        registry yields summary=False with untrusted_signer=True."""
        from fantasyai.aurora.attestation import attest_bundle
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        bundle = self._good_bundle()
        # Forge a signature block (we don't actually verify the
        # signature because that needs cryptography; instead we
        # patch the SDK verify to claim success).
        bundle["integrity"]["signature"] = "deadbeef" * 8
        bundle["integrity"]["public_key"] = "a" * 64

        from aurora_sdk import bundle as sdk_bundle
        # First call verifies integrity (returns True). Second call
        # checks signature with require_signature=True.
        calls = {"n": 0}
        def fake_verify(b, *, require_signature=False):
            calls["n"] += 1
            return True
        monkeypatch.setattr(sdk_bundle, "verify_bundle", fake_verify)
        report = attest_bundle(bundle)
        assert report.integrity_ok is True
        assert report.signature_present is True
        assert report.signature_ok is True
        assert report.untrusted_signer is True
        assert report.summary is False
        assert any("not in the trusted-signer registry" in w
                    for w in report.warnings)

    def test_attest_signed_trusted_signer_passes(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import (
            attest_bundle, add_trusted_signer,
        )
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        bundle = self._good_bundle()
        pk = "b" * 64
        bundle["integrity"]["signature"] = "00" * 64
        bundle["integrity"]["public_key"] = pk
        add_trusted_signer(pk, label="trusted")

        from aurora_sdk import bundle as sdk_bundle
        monkeypatch.setattr(sdk_bundle, "verify_bundle",
                              lambda b, **kw: True)
        report = attest_bundle(bundle)
        assert report.summary is True
        assert report.trusted_signer is True
        assert report.signer_label == "trusted"

    def test_attest_integrity_failure_summary_false(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import attest_bundle
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        bundle = self._good_bundle()
        from aurora_sdk import bundle as sdk_bundle
        monkeypatch.setattr(sdk_bundle, "verify_bundle",
                              lambda b, **kw: False)
        report = attest_bundle(bundle)
        assert report.integrity_ok is False
        assert report.summary is False

    def test_attest_unsigned_bundle_summary_true_when_not_required(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import attest_bundle
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        bundle = self._good_bundle()
        from aurora_sdk import bundle as sdk_bundle
        monkeypatch.setattr(sdk_bundle, "verify_bundle",
                              lambda b, **kw: True)
        report = attest_bundle(bundle)
        # Integrity OK, no signature, not required → summary True.
        assert report.summary is True
        assert report.signature_present is False

    def test_attest_unsigned_bundle_summary_false_when_required(self, monkeypatch, tmp_path):
        from fantasyai.aurora.attestation import attest_bundle
        monkeypatch.setenv("AURORA_DATA_ROOT", str(tmp_path))
        bundle = self._good_bundle()
        from aurora_sdk import bundle as sdk_bundle
        monkeypatch.setattr(sdk_bundle, "verify_bundle",
                              lambda b, **kw: True)
        report = attest_bundle(bundle, require_signature=True)
        assert report.summary is False
        assert any("required" in w for w in report.warnings)


# ----------------------------------------------------------------------
# Stream E: KB marketplace
# ----------------------------------------------------------------------

class TestMarketplaceListing:

    def test_list_marketplace_returns_entries(self):
        from fantasyai.aurora.kb.marketplace import list_marketplace
        packs = list_marketplace()
        # The bundled manifest has at least the 4 starter packs.
        assert len(packs) >= 1
        # Each entry has the canonical fields.
        for p in packs:
            for k in ("id", "name", "description", "version", "install_state"):
                assert k in p

    def test_list_marketplace_marks_state_including_preview(self):
        from fantasyai.aurora.kb.marketplace import list_marketplace
        packs = list_marketplace()
        for p in packs:
            assert p["install_state"] in (
                "available", "installed", "upgrade_available",
                "failed", "preview",
            )

    def test_pending_sha_marks_preview(self):
        """Packs whose sha256 is 'PENDING_FIRST_BUILD' (or missing)
        get install_state=preview so the frontend can disable the
        install button + surface a clear 'not yet released' note."""
        from fantasyai.aurora.kb.marketplace import list_marketplace
        packs = list_marketplace()
        pending = [p for p in packs
                    if (p.get("sha256") or "").upper().startswith("PENDING")]
        # The bundled manifest currently has all 4 packs in PENDING state.
        # We don't pin the count (the manifest can ship real SHAs),
        # but ANY pending pack must be marked "preview".
        for p in pending:
            assert p["install_state"] == "preview", (
                f"pack {p['id']!r} has PENDING sha but state={p['install_state']!r}"
            )

    def test_install_pending_pack_returns_preview_error(self):
        """Installing a PENDING pack returns ok=False + preview=True
        + actionable error — never a silent failure."""
        from fantasyai.aurora.kb.marketplace import list_marketplace, install_pack
        packs = list_marketplace()
        preview_packs = [p for p in packs if p["install_state"] == "preview"]
        if not preview_packs:
            import pytest as _pytest
            _pytest.skip("no preview packs in manifest")
        r = install_pack(preview_packs[0]["id"])
        assert r["ok"] is False
        assert r.get("preview") is True
        assert "not yet released" in (r.get("error") or "").lower()

    def test_install_unknown_pack_returns_clean_error(self):
        from fantasyai.aurora.kb.marketplace import install_pack
        r = install_pack("does_not_exist_pack_xyz")
        assert r["ok"] is False
        assert "not in manifest" in (r.get("error") or "")


# ----------------------------------------------------------------------
# Stream F: GPU embeddings device selection
# ----------------------------------------------------------------------

class TestEmbeddingDevice:

    def setup_method(self, _m):
        from fantasyai.aurora.embedding_device import reset_embedding_device_cache
        reset_embedding_device_cache()

    def test_explicit_cpu(self, monkeypatch):
        from fantasyai.aurora.embedding_device import get_embedding_device
        monkeypatch.setenv("AURORA_EMBEDDINGS_DEVICE", "cpu")
        assert get_embedding_device() == "cpu"

    def test_unknown_value_falls_back_to_cpu(self, monkeypatch):
        from fantasyai.aurora.embedding_device import get_embedding_device
        monkeypatch.setenv("AURORA_EMBEDDINGS_DEVICE", "tpu7000")
        assert get_embedding_device() == "cpu"

    def test_auto_returns_some_device(self, monkeypatch):
        from fantasyai.aurora.embedding_device import get_embedding_device
        monkeypatch.delenv("AURORA_EMBEDDINGS_DEVICE", raising=False)
        d = get_embedding_device()
        assert d in ("cpu", "cuda", "mps")

    def test_cached_until_refresh(self, monkeypatch):
        from fantasyai.aurora.embedding_device import get_embedding_device
        monkeypatch.setenv("AURORA_EMBEDDINGS_DEVICE", "cpu")
        d1 = get_embedding_device()
        # Change env after caching.
        monkeypatch.setenv("AURORA_EMBEDDINGS_DEVICE", "cuda")
        d2 = get_embedding_device()
        assert d1 == d2 == "cpu"   # still cached
        d3 = get_embedding_device(refresh=True)
        # After refresh, the new pref is honoured (falls back to cpu
        # if cuda isn't available, which it likely isn't in CI).
        assert d3 in ("cpu", "cuda")

    def test_info_dict_shape(self):
        from fantasyai.aurora.embedding_device import embedding_device_info
        info = embedding_device_info()
        assert "chosen" in info
        assert "preferred" in info
        assert "cuda_available" in info
        assert "mps_available" in info

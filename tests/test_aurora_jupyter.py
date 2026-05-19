"""
Tests for the Jupyter / notebook surface of aurora_sdk.

Covers:

  * DataFrame input materialisation (the ``run(df)`` flow that writes a
    temp CSV before invoking the pipeline)
  * ``_repr_html_`` on RunResult, Findings, Bundle (renders without
    crashing, produces sensible HTML)
  * The notebook-export tarball: round-trip, integrity hashes,
    defensive extraction
"""
from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aurora_sdk import (
    Bundle,
    Findings,
    export_notebook,
    read_export,
    NotebookExportError,
    EXPORT_EXTENSION,
)
from aurora_sdk.runner import _materialise_dataframe
from aurora_sdk.jupyter import (
    render_runresult_html,
    render_findings_html,
    render_bundle_html,
)


# ===========================================================================
# DataFrame input materialisation
# ===========================================================================

class TestDataFrameMaterialisation:

    def test_dataframe_written_to_csv(self, tmp_path):
        df = pd.DataFrame({
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "value": [1.1, 2.2, 3.3],
        })
        result = _materialise_dataframe(
            df, dataset_name="test.csv", output_root=tmp_path,
        )
        assert isinstance(result, Path)
        assert result.exists()
        # Verify the CSV round-trips.
        roundtrip = pd.read_csv(result)
        assert list(roundtrip.columns) == ["date", "value"]
        assert len(roundtrip) == 3

    def test_string_path_passes_through(self, tmp_path):
        """Strings are returned unchanged (existing path semantics)."""
        result = _materialise_dataframe("data.csv")
        assert result == "data.csv"

    def test_path_object_passes_through(self, tmp_path):
        p = Path("data.csv")
        result = _materialise_dataframe(p)
        assert result == p

    def test_default_dataset_name_includes_timestamp(self, tmp_path):
        df = pd.DataFrame({"a": [1, 2, 3]})
        result = _materialise_dataframe(df, output_root=tmp_path)
        assert result.name.startswith("aurora_df_")
        assert result.suffix == ".csv"

    def test_explicit_name_used_verbatim(self, tmp_path):
        df = pd.DataFrame({"a": [1, 2, 3]})
        result = _materialise_dataframe(
            df, dataset_name="nvda_2024", output_root=tmp_path,
        )
        # Name without .csv gets one appended.
        assert result.name == "nvda_2024.csv"

    def test_non_dataframe_non_path_returns_unchanged(self):
        # Numbers, lists, etc. should pass through (caller will get a
        # FileNotFoundError downstream — that's the expected behaviour).
        for x in (42, [1, 2, 3], None, {"a": 1}):
            assert _materialise_dataframe(x) is x


# ===========================================================================
# _repr_html_ — renders without crashing, produces non-empty HTML
# ===========================================================================

class TestHTMLReprs:

    def _fake_bundle_doc(self):
        """Minimal bundle dict matching the shape aurora_sdk produces."""
        return {
            "bundle_version": "1.0.0",
            "run": {"run_id": "20260519_test"},
            "dataset": {
                "basename": "test.csv",
                "rows": 100,
                "cols": 4,
                "sha256": "a" * 64,
            },
            "integrity": {
                "content_hash": "b" * 64,
            },
            "findings": [
                {"severity": "crit", "confidence": 0.95,
                 "title": "Big anomaly at row 42",
                 "method": "iso-forest", "fabricated": False},
                {"severity": "warn", "confidence": 0.7,
                 "title": "Granger pair (x → y)",
                 "method": "granger", "fabricated": False},
                {"severity": "info", "confidence": 1.0,
                 "title": "4 hidden regimes detected",
                 "method": "hmm_baum_welch", "fabricated": False},
            ],
            "system_model": {"confidence": 0.75},
            "fabricated_count": 0,
        }

    def test_bundle_repr_html_returns_string(self):
        b = Bundle(self._fake_bundle_doc())
        html = b._repr_html_()
        assert isinstance(html, str)
        assert len(html) > 100
        # Sanity: contains key bits from our card template.
        assert "Aurora Bundle" in html
        assert "20260519_test" in html
        # SHA prefix renders.
        assert "bbbbbbbbbbbbbbbb" in html

    def test_bundle_repr_shows_signed_when_signed(self):
        doc = self._fake_bundle_doc()
        doc["integrity"]["signature"] = {"public_key_b64": "abc"}
        b = Bundle(doc)
        html = b._repr_html_()
        assert "signed" in html.lower()

    def test_findings_repr_html_renders_table(self):
        b = Bundle(self._fake_bundle_doc())
        f = b.findings()  # Findings view
        assert isinstance(f, Findings)
        html = f._repr_html_()
        assert "<table" in html
        # All three finding titles should appear.
        assert "Big anomaly at row 42" in html
        assert "Granger" in html
        assert "4 hidden regimes" in html

    def test_findings_repr_html_empty_findings(self):
        f = Findings([])
        html = f._repr_html_()
        assert "Findings: 0" in html

    def test_findings_repr_html_truncates_long_list(self):
        # 50 findings — should cap at MAX_TABLE_ROWS (25) + "+25 more" footer.
        items = [
            {"severity": "info", "confidence": 0.5,
             "title": f"finding {i}", "method": "test", "fabricated": False}
            for i in range(50)
        ]
        f = Findings(items)
        html = f._repr_html_()
        assert "plus 25 more" in html

    def test_runresult_repr_html_via_template(self):
        """We test the renderer function directly with a stub since
        building a real RunResult requires running the pipeline."""
        class StubRunResult:
            confidence = 0.85
            fabricated_count = 0
            bundle = Bundle(self._fake_bundle_doc())
        html = render_runresult_html(StubRunResult())
        assert "Aurora Run" in html
        assert "20260519_test" in html
        assert "0 fabricated" in html
        # Severity pills render for each level present.
        assert "crit 1" in html or "crit" in html.lower()
        assert "warn 1" in html or "warn" in html.lower()

    def test_html_escapes_user_content(self):
        """A finding with HTML-ish content shouldn't break the page or
        inject arbitrary markup."""
        items = [{
            "severity": "warn", "confidence": 0.8,
            "title": "<script>alert(1)</script> hostile",
            "method": "<b>method</b>",
            "fabricated": False,
        }]
        f = Findings(items)
        html = f._repr_html_()
        # Literal <script> should be escaped — not present as raw markup.
        assert "<script>alert(1)</script>" not in html
        # But the escaped version is.
        assert "&lt;script&gt;" in html

    def test_runresult_renderer_handles_missing_fields(self):
        """A partial bundle (e.g., no integrity yet) shouldn't crash."""
        class StubRunResult:
            confidence = None
            fabricated_count = 0
            bundle = Bundle({"bundle_version": "1.0.0"})
        html = render_runresult_html(StubRunResult())
        assert isinstance(html, str)
        assert len(html) > 0


# ===========================================================================
# Notebook export round-trip
# ===========================================================================

def _make_fake_notebook(path: Path) -> None:
    """Write a minimal valid .ipynb file."""
    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Aurora analysis"],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "source": ["import aurora_sdk as aurora\n",
                            "r = aurora.run('data.csv')\n"],
                "outputs": [],
            },
        ],
        "metadata": {"kernelspec": {"name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb, indent=2), encoding="utf-8")


def _make_fake_bundle_path(path: Path) -> Path:
    doc = {
        "bundle_version": "1.0.0",
        "run": {"run_id": "20260519_export_test"},
        "dataset": {"basename": "x.csv", "rows": 10, "cols": 2, "sha256": "c" * 64},
        "integrity": {"content_hash": "d" * 64},
        "findings": [],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


class TestNotebookExport:

    def test_export_produces_valid_tarball(self, tmp_path):
        nb = tmp_path / "analysis.ipynb"
        _make_fake_notebook(nb)
        bundle_path = _make_fake_bundle_path(tmp_path / "bundle.aurora.json")

        info = export_notebook(
            notebook_path=nb,
            bundle=bundle_path,
            output_path=tmp_path / "report",
        )
        assert info.output_path.exists()
        assert str(info.output_path).endswith(EXPORT_EXTENSION)
        assert info.size_bytes > 0
        assert len(info.notebook_sha256) == 64
        assert len(info.bundle_sha256) == 64
        assert info.bundle_content_hash == "d" * 64

    def test_export_with_bundle_object(self, tmp_path):
        """Pass a Bundle instance instead of a path."""
        nb = tmp_path / "analysis.ipynb"
        _make_fake_notebook(nb)
        b = Bundle({
            "bundle_version": "1.0.0",
            "run": {"run_id": "rt"},
            "dataset": {"basename": "x.csv"},
            "integrity": {"content_hash": "e" * 64},
            "findings": [],
        })
        info = export_notebook(nb, b, tmp_path / "out")
        assert info.output_path.exists()
        assert info.bundle_content_hash == "e" * 64

    def test_extension_appended_when_missing(self, tmp_path):
        nb = tmp_path / "analysis.ipynb"
        _make_fake_notebook(nb)
        bundle_path = _make_fake_bundle_path(tmp_path / "bundle.aurora.json")
        info = export_notebook(nb, bundle_path, tmp_path / "report")
        assert str(info.output_path).endswith(".aurora-notebook.tar.gz")

    def test_export_round_trip_via_read(self, tmp_path):
        """Write → read returns the same content."""
        nb = tmp_path / "analysis.ipynb"
        _make_fake_notebook(nb)
        bundle_path = _make_fake_bundle_path(tmp_path / "bundle.aurora.json")
        info = export_notebook(nb, bundle_path, tmp_path / "report")

        extracted = read_export(info.output_path, output_dir=tmp_path / "unpack")
        assert set(extracted.keys()) == {"notebook", "bundle", "manifest"}

        # Notebook bytes match.
        assert extracted["notebook"].read_bytes() == nb.read_bytes()

        # Manifest carries the right hashes.
        manifest = json.loads(extracted["manifest"].read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "1.0.0"
        assert manifest["notebook_sha256"] == info.notebook_sha256
        assert manifest["bundle_sha256"] == info.bundle_sha256

    def test_refuses_missing_notebook(self, tmp_path):
        bundle_path = _make_fake_bundle_path(tmp_path / "bundle.aurora.json")
        with pytest.raises(NotebookExportError, match="notebook not found"):
            export_notebook(tmp_path / "missing.ipynb", bundle_path, tmp_path / "out")

    def test_refuses_non_ipynb_extension(self, tmp_path):
        not_nb = tmp_path / "notes.txt"
        not_nb.write_text("hello")
        bundle_path = _make_fake_bundle_path(tmp_path / "bundle.aurora.json")
        with pytest.raises(NotebookExportError, match="\\.ipynb"):
            export_notebook(not_nb, bundle_path, tmp_path / "out")

    def test_refuses_invalid_notebook_json(self, tmp_path):
        bad_nb = tmp_path / "bad.ipynb"
        bad_nb.write_text("not json{{{")
        bundle_path = _make_fake_bundle_path(tmp_path / "bundle.aurora.json")
        with pytest.raises(NotebookExportError, match="not valid JSON"):
            export_notebook(bad_nb, bundle_path, tmp_path / "out")

    def test_read_export_refuses_unexpected_files(self, tmp_path):
        """An archive with unknown member names should be rejected."""
        archive = tmp_path / "evil.aurora-notebook.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            import io
            for name, payload in (
                ("notebook.ipynb", b'{"cells": [], "nbformat": 4, "nbformat_minor": 5, "metadata": {}}'),
                ("bundle.aurora.json", b'{"integrity": {}}'),
                ("manifest.json", b'{"schema_version": "1.0.0"}'),
                ("evil.sh", b'#!/bin/sh\necho pwned'),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
        with pytest.raises(NotebookExportError, match="unexpected file"):
            read_export(archive, output_dir=tmp_path / "unpack")

    def test_read_export_refuses_path_traversal(self, tmp_path):
        """Defense-in-depth: a malicious archive trying to escape its
        extraction dir must be rejected."""
        archive = tmp_path / "escape.aurora-notebook.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            import io
            info = tarfile.TarInfo("../etc/passwd")
            data = b"oh no"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with pytest.raises(NotebookExportError, match="escape"):
            read_export(archive, output_dir=tmp_path / "unpack")

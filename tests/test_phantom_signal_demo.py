"""
Phantom Signal demo tests — the demo's promises, enforced:

  * runs well under 60 seconds using the precomputed corpus
  * fully seeded: two runs produce byte-identical bundles (content hash)
  * the verdict actually flips to not-identifiable
  * emits a real bundle that aurora_sdk verifies, carrying the corpus hash
  * contains zero fabricated business figures
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import time
from pathlib import Path

import pytest


def _run_demo(tmp_path: Path, sub: str) -> tuple[str, Path]:
    from demos.phantom_signal import run_demo
    out_dir = tmp_path / sub
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run_demo.main(["--fast", "--out", str(out_dir)])
    assert rc == 0
    return buf.getvalue(), out_dir


class TestPhantomSignalDemo:
    @pytest.fixture(scope="class")
    def demo(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("phantom")
        t0 = time.time()
        text, out_dir = _run_demo(tmp, "run1")
        elapsed = time.time() - t0
        return {"text": text, "out": out_dir, "elapsed": elapsed, "tmp": tmp}

    def test_runs_fast(self, demo):
        assert demo["elapsed"] < 60, "demo must run in under a minute"

    def test_three_beats_and_the_flip(self, demo):
        text = demo["text"]
        assert "BEAT 1" in text and "BEAT 2" in text and "BEAT 3" in text
        assert "DECISION: EXECUTE" in text          # the legal action
        assert "NO changepoint" in text             # the ground truth
        assert "NOT IDENTIFIABLE" in text           # the calibrated verdict
        assert "PASS" in text and "FAIL" not in text

    def test_no_fabricated_figures(self, demo):
        text = demo["text"]
        # The standalone prototype invented a decisions-per-month volume.
        # The shipped demo must not: rates come from the corpus, scenario
        # numbers are labelled simulated, and no per-month business
        # volume exists at all.
        assert "decisions/month" not in text
        assert "/month" not in text
        assert "SIMULATED" in text
        assert re.search(r"\d+ seeded", text)  # trial counts are cited

    def test_no_company_claims(self, demo):
        # The demo demonstrates a failure class, not a vendor comparison.
        for banned in ("SAP", "Oracle", "Blue Yonder", "Kinaxis", "o9"):
            assert banned not in demo["text"]

    def test_bundle_verifies_and_carries_corpus_hash(self, demo):
        from aurora_sdk.bundle import load_bundle, verify_bundle
        from fantasyai.aurora.calibration import load_corpus
        bundle_path = demo["out"] / "phantom_signal_bundle.json"
        assert bundle_path.exists()
        doc = load_bundle(bundle_path)
        assert verify_bundle(doc, require_signature=True) is True

        findings = doc["findings"]
        cal = findings[0]["calibration"]
        assert cal["status"] in ("calibrated", "interpolated")
        assert cal["corpus_sha256"] == load_corpus("v1")["_sha256"]
        assert findings[0]["verdict"] == "not_identifiable"
        # Raw statistic still present — contextualised, not erased.
        assert findings[0]["evidence"]["changepoints"]
        # Dataset hash present so a verifier can bind bundle to data.
        assert doc["dataset"]["sha256"]

    def test_fully_seeded_identical_reruns(self, demo):
        text2, out2 = _run_demo(demo["tmp"], "run2")
        b1 = json.loads(
            (demo["out"] / "phantom_signal_bundle.json").read_text(encoding="utf-8")
        )
        b2 = json.loads(
            (out2 / "phantom_signal_bundle.json").read_text(encoding="utf-8")
        )
        assert (
            b1["integrity"]["content_hash"] == b2["integrity"]["content_hash"]
        ), "same seed must produce the same bundle content hash"
        assert b1["dataset"]["sha256"] == b2["dataset"]["sha256"]

    def test_remediation_is_derived_not_generic(self, demo):
        text = demo["text"]
        assert "remediate" in text
        # The confirmation count and the pre-whitening phi are computed
        # from the corpus + fingerprint; both carry digits.
        assert re.search(r"Require \d+ consecutive", text)
        assert re.search(r"phi_hat=0\.\d+", text)

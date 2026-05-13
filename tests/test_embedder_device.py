"""
Embedder device-resolution regression tests.

Pin Aurora's CPU-default policy: the neural embedder must run on CPU
unless the user explicitly opts in via ``AURORA_EMBEDDER_DEVICE=cuda``
AND the GPU is verified compatible with the installed PyTorch.

This is the design that lets Aurora ship as "local AI for analytical
work, runs on user hardware" — every laptop, every Mac, every modest
workstation gets working RAG synthesis. The maintainer's RTX 5070 Ti
(Blackwell sm_120) crashing on stable PyTorch was the canary that
surfaced this universal-default need.

Coverage (8 cases from the brief):
  1. No env var → CPU
  2. AURORA_EMBEDDER_DEVICE=cpu → CPU
  3. AURORA_EMBEDDER_DEVICE=cuda + no torch → CPU + warning
  4. AURORA_EMBEDDER_DEVICE=cuda + incompatible GPU → CPU + warning
  5. AURORA_EMBEDDER_DEVICE=cuda + compatible GPU → CUDA
  6. Unknown device value → CPU + warning
  7. Embedder produces valid 768-dim vectors on CPU
  8. Preflight reports device configuration correctly
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# 1. CPU is the default
# ============================================================

class TestCpuDefault:
    def test_no_env_var_resolves_cpu(self, monkeypatch):
        monkeypatch.delenv("AURORA_EMBEDDER_DEVICE", raising=False)
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            _resolve_device,
        )
        assert _resolve_device() == "cpu"

    def test_explicit_cpu_resolves_cpu(self, monkeypatch):
        monkeypatch.setenv("AURORA_EMBEDDER_DEVICE", "cpu")
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            _resolve_device,
        )
        assert _resolve_device() == "cpu"

    def test_cpu_value_is_case_insensitive(self, monkeypatch):
        for v in ("CPU", "Cpu", "cPu", "  cpu  "):
            monkeypatch.setenv("AURORA_EMBEDDER_DEVICE", v)
            from fantasyai.aurora.knowledge_bank.storage.embedder import (
                _resolve_device,
            )
            assert _resolve_device() == "cpu", f"failed for input {v!r}"


# ============================================================
# 2. cuda requested but unavailable / incompatible → CPU
# ============================================================

class TestCudaFallback:
    def test_cuda_requested_no_torch_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("AURORA_EMBEDDER_DEVICE", "cuda")
        # Make ``import torch`` raise inside _resolve_device.
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("synthetic — torch not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            _resolve_device,
        )
        import logging
        caplog.set_level(logging.WARNING, logger="aurora.embedder")
        assert _resolve_device() == "cpu"
        # Warning emitted so the user knows why CPU was chosen.
        assert any("torch" in r.message.lower() and "cuda" in r.message.lower()
                     for r in caplog.records)

    def test_cuda_requested_no_gpu_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("AURORA_EMBEDDER_DEVICE", "cuda")
        # Inject a fake torch module where cuda.is_available() returns False.
        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: False,
                get_device_capability=lambda *a, **k: (0, 0),
                get_arch_list=lambda: [],
            )
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            _resolve_device,
        )
        import logging
        caplog.set_level(logging.WARNING, logger="aurora.embedder")
        assert _resolve_device() == "cpu"
        assert any("is_available" in r.message
                     or "cuda" in r.message.lower()
                     for r in caplog.records)

    def test_cuda_requested_incompatible_gpu_falls_back(self,
                                                            monkeypatch,
                                                            caplog):
        """Maintainer's exact failure: Blackwell sm_120 not in stable
        torch's arch list (sm_50–sm_90). Must fall back BEFORE the
        kernel error fires."""
        monkeypatch.setenv("AURORA_EMBEDDER_DEVICE", "cuda")
        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                get_device_capability=lambda *a, **k: (12, 0),  # sm_120
                get_device_name=lambda *a, **k: "NVIDIA GeForce RTX 5070 Ti",
                get_arch_list=lambda: ["sm_50", "sm_60", "sm_70",
                                          "sm_75", "sm_80", "sm_86",
                                          "sm_90"],
            )
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            _resolve_device,
        )
        import logging
        caplog.set_level(logging.WARNING, logger="aurora.embedder")
        assert _resolve_device() == "cpu"
        # The warning text must mention the actual sm_X capability AND
        # the arch list, so users can diagnose the mismatch from logs.
        msg = " ".join(r.message for r in caplog.records).lower()
        assert "sm_120" in msg
        assert "sm_90" in msg or "arch" in msg

    def test_cuda_requested_compatible_gpu_activates(self, monkeypatch):
        """When the GPU's capability IS in arch_list, _resolve_device
        returns 'cuda' — the GPU opt-in works."""
        monkeypatch.setenv("AURORA_EMBEDDER_DEVICE", "cuda")
        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                get_device_capability=lambda *a, **k: (8, 6),  # sm_86
                get_device_name=lambda *a, **k: "NVIDIA RTX A6000",
                get_arch_list=lambda: ["sm_50", "sm_60", "sm_70",
                                          "sm_75", "sm_80", "sm_86",
                                          "sm_90"],
            )
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            _resolve_device,
        )
        assert _resolve_device() == "cuda"


# ============================================================
# 3. Invalid input → CPU
# ============================================================

class TestUnknownDevice:
    def test_unknown_value_falls_back_to_cpu(self, monkeypatch, caplog):
        monkeypatch.setenv("AURORA_EMBEDDER_DEVICE", "tpu")
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            _resolve_device,
        )
        import logging
        caplog.set_level(logging.WARNING, logger="aurora.embedder")
        assert _resolve_device() == "cpu"
        assert any("unknown" in r.message.lower()
                     for r in caplog.records)


# ============================================================
# 4. Embedder produces valid 768-d vectors on CPU
# ============================================================

class TestEmbedderProducesValidVectors:
    def test_cpu_embedder_returns_unit_vector_of_known_dim(self):
        """Use the always-available TF-IDF fallback to assert the
        general embedder contract: a non-empty input produces a
        non-zero vector of the embedder's stated dim. (When the
        maintainer's environment has sentence-transformers installed,
        the same contract holds with dim=768.)"""
        from fantasyai.aurora.knowledge_bank.storage.embedder import (
            HashedTfIdfEmbedder,
        )
        emb = HashedTfIdfEmbedder(dim=768)
        v = emb.embed("damped harmonic oscillator with period 24h")
        assert len(v) == 768
        # Non-zero (real text → real vector).
        assert any(x != 0 for x in v)
        # L2-normalised (within numerical tolerance).
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-6


# ============================================================
# 5. Preflight reports device configuration
# ============================================================

class TestPreflightDeviceReport:
    def test_preflight_includes_embedder_device_block(self, monkeypatch):
        monkeypatch.delenv("AURORA_EMBEDDER_DEVICE", raising=False)
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        assert "embedder_device" in rep["checks"]
        ed = rep["checks"]["embedder_device"]
        # Required diagnostic fields.
        for key in ("configured_device", "active_device",
                      "gpu_present", "guidance"):
            assert key in ed, f"preflight missing {key}"
        assert ed["active_device"] == "cpu"

    def test_preflight_no_gpu_guidance(self, monkeypatch):
        """Sandbox case: torch absent → guidance must say CPU is
        the expected configuration, not push GPU."""
        monkeypatch.delenv("AURORA_EMBEDDER_DEVICE", raising=False)
        # Ensure no torch in this preflight call.
        monkeypatch.setitem(sys.modules, "torch", None)
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        ed = rep["checks"]["embedder_device"]
        guidance = (ed.get("guidance") or "").lower()
        # Reject any guidance that pushes GPU as a remediation when
        # there's no GPU. CPU must be presented as correct.
        assert "expected configuration" in guidance or "cpu" in guidance

    def test_preflight_incompatible_gpu_says_cpu_correct(self, monkeypatch):
        """The maintainer's exact case: sm_120 GPU + stable torch.
        Preflight must say CPU is the correct choice for now."""
        monkeypatch.delenv("AURORA_EMBEDDER_DEVICE", raising=False)
        fake_torch = types.SimpleNamespace(
            __version__="2.11.0+cu121",
            cuda=types.SimpleNamespace(
                is_available=lambda: True,
                get_device_capability=lambda *a, **k: (12, 0),
                get_device_name=lambda *a, **k: "NVIDIA GeForce RTX 5070 Ti",
                get_device_properties=lambda *a, **k: types.SimpleNamespace(
                    total_memory=int(16e9)),
                get_arch_list=lambda: ["sm_50", "sm_60", "sm_70",
                                          "sm_75", "sm_80", "sm_86",
                                          "sm_90"],
            ),
        )
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        ed = rep["checks"]["embedder_device"]
        assert ed["gpu_present"] is True
        assert ed["gpu_compatible"] is False
        # The active device is still CPU (default).
        assert ed["active_device"] == "cpu"
        # Guidance says CPU is correct + warns against opt-in.
        guidance = ed.get("guidance", "").lower()
        assert "cpu" in guidance
        assert "do not" in guidance or "do not set" in guidance \
                 or "correct" in guidance

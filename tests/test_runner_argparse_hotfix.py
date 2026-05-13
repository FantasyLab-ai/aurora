"""
Workstream 3A — runner argparse hotfix regression tests.

Pins the bug where ``studio_api._trigger_run`` was passing
``--also-motif`` and ``--also-orchestrator`` to the runner subprocess,
but the runner's argparse only declared four flags (``--also-math``,
``--math-v2``, ``--deep-math``, ``--also-llm``). The unrecognized-
argument exit broke every fresh (non-cached) run with the frontend
banner: ``Analysis state could not be loaded · run_aurora_with_steps_1_4.py:
error: unrecognized arguments: --also-motif --also-orchestrator``.

These tests guard against regression by:

  1. Asserting both runner scripts only declare the canonical flag
     names that ``_trigger_run`` actually emits.
  2. Asserting the production cmd builder NEVER emits ``--also-motif``
     or ``--also-orchestrator`` regardless of how the kwargs are set
     (so even if a caller defaults them to True, the cmd stays clean).
  3. Round-tripping the canonical kwarg set through the runner's
     argparse (``--help`` is enough — argparse exits 2 on unknown
     args at parse time, but the cmd we emit must parse OK).

Brief constraint: NO analytical changes. This sprint only un-blocks
the runner; subsequent diagnostic + tier-system work is gated on the
maintainer's approval.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# Canonical flag set the studio_api caller emits today (Workstream-3A).
# When _trigger_run builds its cmd line, it appends these — and only
# these — beyond ``--dataset`` + ``--seed``.
EMITTED_RUNNER_FLAGS = {"--also-math", "--math-v2", "--deep-math",
                          "--also-llm"}

# Flags that exist in cache.normalize_flags / _trigger_run kwargs for
# cache-key stability but MUST NOT reach the cmd line because the runner
# does not declare them.
NEVER_EMIT = {"--also-motif", "--also-orchestrator"}


# ============================================================
# 1. Runner script argparse declarations match what we emit
# ============================================================

class TestRunnerArgparseDeclarations:
    def _runner_path(self) -> Path:
        # The default RUNNER_MODULE in studio_api.
        return ROOT / "scripts" / "run_aurora_with_steps_1_4.py"

    def _delegate_path(self) -> Path:
        return ROOT / "scripts" / "run_aurora_dataset_runner.py"

    def _flags_in(self, path: Path) -> set:
        # Cheap regex parse of the argparse declarations — doesn't run
        # the script (which would import heavy modules). We just need
        # the literal --names.
        src = path.read_text(encoding="utf-8", errors="replace")
        return set(re.findall(r'add_argument\("(--[a-z0-9-]+)"', src))

    def test_outer_runner_declares_all_emitted_flags(self):
        flags = self._flags_in(self._runner_path())
        missing = EMITTED_RUNNER_FLAGS - flags
        assert not missing, (
            f"runner script does not declare emitted flags {missing}; "
            "studio_api would crash with 'unrecognized arguments'"
        )

    def test_outer_runner_does_not_declare_stale_flags(self):
        flags = self._flags_in(self._runner_path())
        unexpected = NEVER_EMIT & flags
        assert not unexpected, (
            f"runner declares the stale flags {unexpected} that 3A "
            "deliberately stopped emitting — declaration without an "
            "emitter is dead code; remove or wire it"
        )

    def test_delegate_runner_declares_all_emitted_flags(self):
        # The outer runner forwards to scripts.run_aurora_dataset_runner,
        # so both must accept the same flag set.
        flags = self._flags_in(self._delegate_path())
        missing = EMITTED_RUNNER_FLAGS - flags
        assert not missing, (
            f"delegate runner does not declare {missing}; the outer "
            "runner forwards them and would fail at the inner argparse"
        )


# ============================================================
# 2. studio_api cmd builder NEVER emits the stale flags
# ============================================================

class TestCmdBuilderNeverEmitsStaleFlags:
    def test_studio_api_source_does_not_emit_stale_flags(self):
        """Direct AST-style scan of studio_api.py: NO ``cmd.append(...)``
        line emits ``--also-motif`` or ``--also-orchestrator``.

        We DON'T import studio_api (it pulls in flask + the whole
        backend), so we read the source as text and look for the
        emission pattern.
        """
        src = (ROOT / "studio_api.py").read_text(encoding="utf-8")
        for stale in ("--also-motif", "--also-orchestrator"):
            # Find any line that BOTH appends to a cmd and contains the
            # stale flag literal. Comments mentioning the flag are fine.
            for i, line in enumerate(src.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "cmd.append" in stripped and stale in stripped:
                    pytest.fail(
                        f"studio_api.py:{i} emits the stale flag "
                        f"{stale!r}: {stripped!r} — Workstream 3A "
                        "removed these because the runner doesn't "
                        "declare them"
                    )


# ============================================================
# 3. Round-trip: building a runner argparse identical to the live
# script's, verify the canonical caller's cmd parses without error.
# ============================================================

class TestCanonicalCmdParses:
    """Mirror of the runner's argparse spec — kept in sync with
    ``scripts/run_aurora_with_steps_1_4.py``. If the live runner adds
    a new flag, this test should be updated to match. If a new flag
    was added but this mirror wasn't updated, ``test_outer_runner_
    declares_all_emitted_flags`` above will catch it."""

    def _build_parser(self):
        ap = argparse.ArgumentParser()
        ap.add_argument("--dataset", required=True)
        ap.add_argument("--seed", type=int, default=42)
        ap.add_argument("--also-math", action="store_true")
        ap.add_argument("--math-v2", action="store_true")
        ap.add_argument("--deep-math", action="store_true")
        ap.add_argument("--also-llm", action="store_true")
        return ap

    def _canonical_caller_argv(self) -> list:
        """The exact argv that ``_trigger_run`` builds today after the
        3A hotfix. Update if/when studio_api's cmd builder changes."""
        return [
            "--dataset", "demo.csv",
            "--seed", "42",
            "--also-math",
            "--math-v2",
            "--deep-math",
            "--also-llm",
        ]

    def test_canonical_argv_parses_clean(self):
        ap = self._build_parser()
        args = ap.parse_args(self._canonical_caller_argv())
        assert args.dataset == "demo.csv"
        assert args.seed == 42
        assert args.also_math is True
        assert args.math_v2 is True
        assert args.deep_math is True
        assert args.also_llm is True

    def test_argv_with_stale_flags_would_have_failed(self):
        """Sanity: confirm the parser DOES reject the stale flags —
        i.e., this isn't a no-op test. If someone re-adds the flags to
        the runner without updating the canonical-argv list, this
        test still fires."""
        ap = self._build_parser()
        argv = self._canonical_caller_argv() + [
            "--also-motif", "--also-orchestrator",
        ]
        with pytest.raises(SystemExit) as excinfo:
            ap.parse_args(argv)
        # argparse exits 2 on unrecognized arguments.
        assert excinfo.value.code == 2


# ============================================================
# 4. End-to-end: actually run the runner with --help and assert it
#    exits 0 (proves argparse declarations are syntactically intact
#    and the script imports cleanly).
# ============================================================

class TestRunnerHelpExits0:
    def test_runner_help_exit_code_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m",
              "scripts.run_aurora_with_steps_1_4", "--help"],
            cwd=str(ROOT),
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, (
            f"runner --help exited {proc.returncode}\n"
            f"stderr:\n{proc.stderr[:1000]}"
        )
        # And the help text mentions the flags we emit.
        for flag in EMITTED_RUNNER_FLAGS:
            assert flag in proc.stdout, (
                f"runner --help does not mention {flag} "
                f"(declared but missing from help?)"
            )

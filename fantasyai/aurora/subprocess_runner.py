# fantasyai/aurora/subprocess_runner.py
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class ModuleRunResult:
    module: str
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    parsed_json_blocks: List[Dict[str, Any]]


_JSON_START = re.compile(r"[\{\[]")


def _extract_json_blocks(text: str, max_blocks: int = 5) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    starts = [m.start() for m in _JSON_START.finditer(text)]

    for s in starts:
        if len(blocks) >= max_blocks:
            break
        for e in range(min(len(text), s + 20000), s + 10, -1):
            snippet = text[s:e].strip()
            if not snippet:
                continue
            if snippet[-1] not in ("}", "]"):
                continue
            try:
                obj = json.loads(snippet)
                blocks.append(obj if isinstance(obj, dict) else {"_json": obj})
                break
            except Exception:
                continue

    uniq: List[Dict[str, Any]] = []
    seen = set()
    for b in blocks:
        key = json.dumps(b, sort_keys=True, default=str)
        if key not in seen:
            uniq.append(b)
            seen.add(key)
    return uniq


def _repo_root() -> Path:
    # fantasyai/aurora/subprocess_runner.py -> repo root is 3 parents up
    # .../fantasyai/aurora/subprocess_runner.py
    return Path(__file__).resolve().parents[2]


def run_python_module(module: str, timeout_s: int = 180) -> ModuleRunResult:
    """
    Run `python -m <module>` using the *current interpreter* (venv-safe).
    """
    proc = subprocess.run(
        [sys.executable, "-m", module],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=str(_repo_root()),
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(_repo_root())},
    )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    parsed = _extract_json_blocks(stdout)

    return ModuleRunResult(
        module=module,
        ok=(proc.returncode == 0),
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        parsed_json_blocks=parsed,
    )

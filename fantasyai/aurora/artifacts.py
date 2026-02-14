from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_name(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s[:80] if s else "item"


def _now_run_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


def default_runs_root() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "outputs" / "aurora_runs"


@dataclass
class AuroraRun:
    domain: str
    run_id: str = field(default_factory=_now_run_id)
    root_dir: Path = field(default_factory=default_runs_root)
    tags: Dict[str, Any] = field(default_factory=dict)

    blocks: List[Dict[str, Any]] = field(default_factory=list)
    texts: Dict[str, str] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=dict)  # logical_name -> relpath

    def path(self) -> Path:
        return self.root_dir / self.run_id

    def plots_dir(self) -> Path:
        return self.path() / "plots"

    def ensure_dirs(self) -> None:
        self.path().mkdir(parents=True, exist_ok=True)
        self.plots_dir().mkdir(parents=True, exist_ok=True)

    def add(self, name: str, obj: Any) -> None:
        self.blocks.append({"name": _safe_name(name), "data": obj})

    def add_text(self, name: str, text: str) -> None:
        self.texts[_safe_name(name)] = text

    def write_file(self, logical_name: str, content: bytes, relpath: str) -> str:
        self.ensure_dirs()
        relpath = relpath.replace("\\", "/").lstrip("/")
        out_path = self.path() / relpath
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)
        self.files[_safe_name(logical_name)] = relpath
        return str(out_path)

    def write_plot_bytes(self, logical_name: str, png_bytes: bytes) -> str:
        fname = f"{_safe_name(logical_name)}.png"
        return self.write_file(logical_name, png_bytes, f"plots/{fname}")

    def finalize(self) -> Dict[str, str]:
        self.ensure_dirs()

        meta = {
            "run_id": self.run_id,
            "domain": self.domain,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "tags": self.tags,
            "files": self.files,
        }

        summary = {"meta": meta, "blocks": self.blocks}

        meta_path = self.path() / "meta.json"
        summary_path = self.path() / "summary.json"
        report_path = self.path() / "report.md"

        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        md: List[str] = []
        md.append(f"# Aurora Report — {self.domain}")
        md.append("")
        md.append(f"**Run ID:** `{self.run_id}`  ")
        md.append(f"**Created:** {meta['created_at']}  ")
        if self.tags:
            md.append(f"**Tags:** `{json.dumps(self.tags)}`")
        md.append("")

        if self.texts:
            for k, v in self.texts.items():
                md.append(f"## {k.replace('_',' ').title()}")
                md.append("")
                md.append(v.strip())
                md.append("")
        else:
            md.append("_No narrative sections were produced for this run._")
            md.append("")

        report_path.write_text("\n".join(md).rstrip() + "\n", encoding="utf-8")

        return {
            "run_dir": str(self.path()),
            "meta_json": str(meta_path),
            "summary_json": str(summary_path),
            "report_md": str(report_path),
        }


def latest_run_dir(root: Optional[Path] = None) -> Optional[Path]:
    root = root or default_runs_root()
    if not root.exists():
        return None
    runs = [p for p in root.iterdir() if p.is_dir()]
    if not runs:
        return None
    return sorted(runs, key=lambda p: p.name)[-1]

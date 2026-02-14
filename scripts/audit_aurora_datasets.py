from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "fantasyai" / "aurora" / "datasets" / "registry.json"

def read_sig(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")

def list_csv_like(folder: Path):
    if not folder.exists() or not folder.is_dir():
        return []
    files = list(folder.rglob("*.csv")) + list(folder.rglob("*.csv.gz"))
    out = []
    for f in files:
        try:
            if f.stat().st_size > 200:
                out.append(f)
        except Exception:
            pass
    return sorted(out, key=lambda p: str(p).lower())

def main():
    reg = json.loads(read_sig(REGISTRY_PATH))
    base_dir = Path(reg.get("base_dir", "")).expanduser()
    if str(base_dir).strip() == "":
        base_dir = ROOT
    elif not base_dir.is_absolute():
        base_dir = (ROOT / base_dir).resolve()

    ds = reg.get("datasets", [])
    rows = []

    if isinstance(ds, dict):
        items = [{"key": k, **v} for k, v in ds.items()]
    else:
        items = ds

    for item in items:
        key = str(item.get("key", "")).strip() or str(item.get("key", "")).strip()
        enabled = bool(item.get("enabled", True))
        p = Path(item.get("path", "")).expanduser()
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        files = list_csv_like(p) if enabled else []
        rows.append((key, enabled, str(p), len(files)))

    rows.sort(key=lambda x: (-x[3], x[0]))

    print("\n[AURORA DATASETS] Registry audit")
    for key, enabled, p, n in rows:
        flag = "EN" if enabled else "DIS"
        note = "" if n > 0 else " (EMPTY / NO CSV FOUND)"
        print(f"- {key:28} [{flag}] files={n:3d} path={p}{note}")

if __name__ == "__main__":
    main()

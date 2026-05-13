"""
Multi-format dataset loader.

Auto-detects format from extension (and content-sniff for ambiguous cases) and
returns a single ``LoadResult`` with the DataFrame plus provenance metadata.
Supported today:

  - CSV / TSV (incl. .gz / .bz2 / .zip via pandas)
  - JSON (records OR object-of-arrays OR single object)
  - JSONL (newline-delimited JSON)
  - Parquet (requires ``pyarrow`` or ``fastparquet``)
  - Excel .xlsx / .xls (requires ``openpyxl`` or ``xlrd``)

Design choices for "messy real-world data":

  * Never raise on encoding hiccups: try utf-8, then latin-1, then errors='replace'.
  * Never raise on a truly unreadable file: surface a structured ``LoadError`` so
    the runner reports it in the artifact contract instead of crashing.
  * Always record the *detected* format and the *resolved* delimiter (for CSVs)
    so downstream layers can reason about parser fidelity.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd


SUPPORTED_EXT = {
    ".csv", ".tsv", ".txt",
    ".json", ".jsonl", ".ndjson",
    ".parquet", ".pq",
    ".xlsx", ".xls",
    ".gz", ".bz2", ".zip",
}


def supported_formats() -> List[str]:
    return sorted(SUPPORTED_EXT)


@dataclass
class LoadResult:
    df: pd.DataFrame
    path: str
    format: str            # one of: csv, tsv, json, jsonl, parquet, excel
    rows: int
    cols: int
    size_bytes: int
    encoding: Optional[str] = None
    delimiter: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_meta(self) -> Dict[str, Any]:
        """Provenance metadata; never embeds the dataframe itself."""
        d = asdict(self)
        d.pop("df", None)
        return d


class LoadError(Exception):
    """Structured load failure carrying enough context for honest reporting."""
    def __init__(self, message: str, *, path: str = "", suggestion: str = ""):
        super().__init__(message)
        self.path = path
        self.suggestion = suggestion

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "path": self.path,
            "suggestion": self.suggestion,
        }


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _detect_format(path: Path) -> str:
    """Return one of: csv, tsv, json, jsonl, parquet, excel.

    Strategy: extension first; fall back to content sniffing for .txt /
    extension-less files.
    """
    name = path.name.lower()

    # Strip outer compression layer for extension purposes.
    inner = name
    for compressed in (".gz", ".bz2", ".zip"):
        if inner.endswith(compressed):
            inner = inner[: -len(compressed)]
            break

    if inner.endswith(".csv"):
        return "csv"
    if inner.endswith(".tsv"):
        return "tsv"
    if inner.endswith(".jsonl") or inner.endswith(".ndjson"):
        return "jsonl"
    if inner.endswith(".json"):
        return "json"
    if inner.endswith(".parquet") or inner.endswith(".pq"):
        return "parquet"
    if inner.endswith(".xlsx") or inner.endswith(".xls"):
        return "excel"

    # Fall back to content sniff.
    return _sniff_format(path)


def _sniff_format(path: Path) -> str:
    """Read the first KB and guess. Conservative — defaults to CSV on ambiguity."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return "csv"  # conservative; later open will raise the real error

    # Parquet magic bytes: 'PAR1' at start (or end, but start is common for sniffing).
    if head[:4] == b"PAR1":
        return "parquet"

    # Excel magic bytes: 'PK' (xlsx is a zip) or D0CF (xls compound document).
    if head[:2] == b"PK" or head[:4] == b"\xd0\xcf\x11\xe0":
        return "excel"

    text = head.decode("utf-8", errors="replace").lstrip()

    if not text:
        return "csv"

    # JSONL: each line is its own JSON value.
    if text[0] in "{[" and "\n" in text:
        first_line = text.split("\n", 1)[0].strip()
        if first_line.endswith("}") or first_line.endswith("]"):
            try:
                json.loads(first_line)
                return "jsonl"
            except json.JSONDecodeError:
                pass
    if text[0] in "{[":
        return "json"

    # Tab-heavy → tsv, comma-heavy → csv.
    sample = text[:1024]
    if sample.count("\t") >= 2 and sample.count("\t") > sample.count(","):
        return "tsv"
    return "csv"


# ---------------------------------------------------------------------------
# Format-specific loaders (each returns df + notes)
# ---------------------------------------------------------------------------

def _read_text_with_fallback(path: Path) -> tuple[str, str]:
    """Read text trying utf-8 first, then latin-1, then replace."""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace"), "utf-8/replace"


def _load_csv_or_tsv(path: Path, fmt: str) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Load CSV/TSV with sniffed delimiter and forgiving encoding."""
    notes: List[str] = []
    sep = "\t" if fmt == "tsv" else None  # None lets pandas sniff

    # First pass: try the obvious thing.
    last_err: Optional[Exception] = None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(
                path, sep=sep, engine="python",
                on_bad_lines="warn", encoding=enc,
            )
            delim = sep
            if delim is None and df.shape[1] == 1:
                # Pandas didn't pick a delimiter — sniff manually.
                text_head, _ = _read_text_with_fallback(path)
                try:
                    dialect = csv.Sniffer().sniff(text_head[:4096], delimiters=",;\t|")
                    df = pd.read_csv(path, sep=dialect.delimiter, encoding=enc, on_bad_lines="warn")
                    delim = dialect.delimiter
                    notes.append(f"delimiter_sniffed: {repr(delim)}")
                except csv.Error:
                    notes.append("delimiter_sniff_failed_using_pandas_default")
            return df, {"encoding": enc, "delimiter": delim or ",", "notes": notes}
        except Exception as e:
            last_err = e
    raise LoadError(f"CSV/TSV parse failed: {last_err!s}", path=str(path),
                    suggestion="Check encoding or delimiter; try opening in a text editor.")


def _load_json(path: Path) -> tuple[pd.DataFrame, Dict[str, Any]]:
    text, enc = _read_text_with_fallback(path)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise LoadError(f"JSON parse failed at line {e.lineno}, col {e.colno}: {e.msg}",
                        path=str(path),
                        suggestion="Is it actually JSONL? Try renaming to .jsonl.")
    notes: List[str] = []
    if isinstance(obj, list):
        df = pd.DataFrame(obj)
        notes.append("interpreted_as: list_of_records")
    elif isinstance(obj, dict):
        # Two common shapes: {"col1": [...], "col2": [...]} or a single record.
        if all(isinstance(v, list) for v in obj.values()) and obj:
            df = pd.DataFrame(obj)
            notes.append("interpreted_as: dict_of_columns")
        else:
            df = pd.DataFrame([obj])
            notes.append("interpreted_as: single_record")
    else:
        raise LoadError("JSON root must be array or object", path=str(path),
                        suggestion="Wrap your data in [] or {}.")
    return df, {"encoding": enc, "notes": notes}


def _load_jsonl(path: Path) -> tuple[pd.DataFrame, Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    bad = 0
    enc = "utf-8"
    open_fn: Any
    if path.suffix.lower() == ".gz" or path.name.lower().endswith(".jsonl.gz"):
        open_fn = lambda: gzip.open(path, "rt", encoding="utf-8", errors="replace")
    else:
        # Use the text-fallback helper to pick an encoding once.
        _, enc = _read_text_with_fallback(path)
        open_fn = lambda: open(path, "r", encoding=enc, errors="replace")

    with open_fn() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if not rows:
        raise LoadError("JSONL file produced 0 rows", path=str(path),
                        suggestion="Verify each line is a standalone JSON object.")
    df = pd.json_normalize(rows, max_level=1)
    notes: List[str] = []
    if bad:
        notes.append(f"skipped_unparseable_lines: {bad}")
    return df, {"encoding": enc, "notes": notes}


def _load_parquet(path: Path) -> tuple[pd.DataFrame, Dict[str, Any]]:
    try:
        df = pd.read_parquet(path)
    except ImportError as e:
        raise LoadError(f"Parquet support unavailable: {e!s}", path=str(path),
                        suggestion="Install 'pyarrow' or 'fastparquet'.")
    except Exception as e:
        raise LoadError(f"Parquet read failed: {e!s}", path=str(path))
    return df, {"notes": []}


def _load_excel(path: Path) -> tuple[pd.DataFrame, Dict[str, Any]]:
    try:
        # If multiple sheets, take the first non-empty one and record the choice.
        all_sheets = pd.read_excel(path, sheet_name=None)
    except ImportError as e:
        raise LoadError(f"Excel support unavailable: {e!s}", path=str(path),
                        suggestion="Install 'openpyxl' (for .xlsx) or 'xlrd' (for legacy .xls).")
    except Exception as e:
        raise LoadError(f"Excel read failed: {e!s}", path=str(path))
    notes: List[str] = []
    if not all_sheets:
        raise LoadError("Excel file has no sheets", path=str(path))
    # Pick first non-empty sheet.
    chosen_name, chosen_df = next(
        ((n, d) for n, d in all_sheets.items() if not d.empty),
        next(iter(all_sheets.items())),
    )
    if len(all_sheets) > 1:
        notes.append(f"multiple_sheets_found: {list(all_sheets.keys())}; chose: {chosen_name!r}")
    return chosen_df, {"notes": notes}


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def load_dataset(path: Union[str, Path]) -> LoadResult:
    """Load *any* tabular dataset by path.

    Raises ``LoadError`` only when the format is fundamentally unparseable.
    Encoding/delimiter quirks are handled internally and recorded in ``notes``.
    """
    p = Path(path)
    if not p.exists():
        raise LoadError("File does not exist", path=str(p))
    if not p.is_file():
        raise LoadError("Path is not a file", path=str(p),
                        suggestion="Pass a CSV/JSON/Parquet/Excel file, not a directory.")

    fmt = _detect_format(p)
    size = p.stat().st_size

    if fmt in ("csv", "tsv"):
        df, meta = _load_csv_or_tsv(p, fmt)
    elif fmt == "json":
        df, meta = _load_json(p)
    elif fmt == "jsonl":
        df, meta = _load_jsonl(p)
    elif fmt == "parquet":
        df, meta = _load_parquet(p)
    elif fmt == "excel":
        df, meta = _load_excel(p)
    else:
        raise LoadError(f"Unsupported format: {fmt}", path=str(p),
                        suggestion=f"Supported: {', '.join(supported_formats())}")

    return LoadResult(
        df=df,
        path=str(p),
        format=fmt,
        rows=int(df.shape[0]),
        cols=int(df.shape[1]),
        size_bytes=int(size),
        encoding=meta.get("encoding"),
        delimiter=meta.get("delimiter"),
        notes=list(meta.get("notes", [])),
    )

from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd

def _read_csv_safely(inp: Path, sep: str = ",", decimal: str = ".", encoding: str | None = None) -> pd.DataFrame:
    # Pandas quirk: python engine doesn't support low_memory, and decimal cannot be None.
    dec = decimal if decimal else "."
    enc = encoding if encoding else "utf-8"

    # Try fast engine first
    try:
        return pd.read_csv(
            inp,
            sep=sep,
            decimal=dec,
            engine="c",
            encoding=enc,
            on_bad_lines="skip",
        )
    except Exception:
        # Fallback python engine for messy files
        return pd.read_csv(
            inp,
            sep=sep,
            decimal=dec,
            engine="python",
            encoding=enc,
            on_bad_lines="skip",
        )

def _read_excel(inp: Path) -> pd.DataFrame:
    # openpyxl installed by the PS wrapper
    return pd.read_excel(inp)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--sep", default=",")
    ap.add_argument("--decimal", default=".")
    ap.add_argument("--encoding", default=None)
    args = ap.parse_args()

    inp = Path(args.inp)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    suf = inp.suffix.lower()
    if suf in [".xlsx", ".xls"]:
        df = _read_excel(inp)
    else:
        df = _read_csv_safely(inp, sep=args.sep, decimal=args.decimal, encoding=args.encoding)

    # Normalize column names lightly (optional, keeps originals mostly intact)
    df.columns = [c.strip() for c in df.columns]

    df.to_csv(out, index=False)
    print(f"[OK] wrote {out} rows={len(df)} cols={df.shape[1]}")

if __name__ == "__main__":
    main()
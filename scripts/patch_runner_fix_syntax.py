import argparse, re, sys
from pathlib import Path
from datetime import datetime

def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def ensure_strip_think(text: str) -> str:
    # If _strip_think is missing, insert it just before def _ollama_generate(...)
    if re.search(r'(?m)^\s*def\s+_strip_think\s*\(', text):
        return text

    anchor = re.search(r'(?m)^\s*def\s+_ollama_generate\s*\(', text)
    if not anchor:
        die("Could not find 'def _ollama_generate(' anchor to insert _strip_think().")

    insert = r'''
def _strip_think(s: str) -> str:
    """
    Remove hidden reasoning blocks (e.g. <think>...</think>) that some models return.
    Keeps user-facing narrative clean.
    """
    if not s:
        return ""
    s = re.sub(r"(?is)<think>.*?</think>", "", s)
    # Some models may include other tags; keep it conservative.
    return s.strip()

'''
    i = anchor.start()
    return text[:i] + insert.lstrip("\n") + text[i:]

def fix_load_df_signature(text: str) -> str:
    # Fix the exact broken line currently in your file:
    # def _load_dftable_path: str) -> pd.DataFrame:
    text = re.sub(
        r'(?m)^\s*def\s+_load_df\s*table_path\s*:\s*str\)\s*->\s*pd\.DataFrame\s*:\s*$',
        'def _load_df(table_path: str) -> pd.DataFrame:',
        text
    )
    text = re.sub(
        r'(?m)^\s*def\s+_load_dftable_path\s*:\s*str\)\s*->\s*pd\.DataFrame\s*:\s*$',
        'def _load_df(table_path: str) -> pd.DataFrame:',
        text
    )
    return text

def fix_read_csv_var(text: str) -> str:
    # Ensure the function body uses table_path, not an undefined name
    text = re.sub(
        r'(?m)^\s*return\s+pd\.read_csv\(\s*table_path\s*\)\s*$',
        '    return pd.read_csv(table_path)',
        text
    )
    text = re.sub(
        r'(?m)^\s*return\s+pd\.read_csv\(\s*table_path\s*\)\s*$',
        '    return pd.read_csv(table_path)',
        text
    )
    # If it accidentally became "return pd.read_csv(table_path)" without indent, fix it inside the function
    text = re.sub(
        r'(?m)^(return\s+pd\.read_csv\(\s*table_path\s*\)\s*)$',
        r'    \1',
        text
    )
    return text

def comment_stray_stdout_line(text: str) -> str:
    # This line exists in your file and causes syntax errors:
    #  (so you always see it ran)
    text = re.sub(
        r'(?m)^\s*\(so you always see it ran\)\s*$',
        '    # (so you always see it ran)',
        text
    )
    # Also fix the preceding comment if it got split strangely
    text = re.sub(
        r'(?m)^\s*#\s*FINAL\s+stdout\s+summary\s*$',
        '    # FINAL stdout summary',
        text
    )
    return text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        die(f"file not found: {p}")

    original = p.read_text(encoding="utf-8", errors="replace")

    # Backup
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{stamp}")
    bak.write_text(original, encoding="utf-8")
    print(f"[OK] backup -> {bak}")

    text = original

    # Apply fixes
    text = fix_load_df_signature(text)
    text = fix_read_csv_var(text)
    text = comment_stray_stdout_line(text)
    text = ensure_strip_think(text)

    # Safety: ensure _load_df exists now (since _main calls it)
    if not re.search(r'(?m)^\s*def\s+_load_df\s*\(\s*table_path\s*:\s*str\s*\)\s*->\s*pd\.DataFrame\s*:\s*$', text):
        die("After patch, def _load_df(table_path: str) -> pd.DataFrame: still not found. No write performed.")

    # Write patched file
    p.write_text(text, encoding="utf-8")
    print(f"[OK] wrote -> {p}")

    # Compile check
    import py_compile
    try:
        py_compile.compile(str(p), doraise=True)
        print("[OK] py_compile passed")
    except Exception as e:
        # Restore backup on failure
        p.write_text(original, encoding="utf-8")
        print("[RESTORED] compile failed; file restored to backup", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()

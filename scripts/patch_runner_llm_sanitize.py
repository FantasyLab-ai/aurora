import re
import argparse
from pathlib import Path

STRIP_THINK_DEF = r"""
def _strip_think(text: str) -> str:
    \"\"\"Remove DeepSeek-style <think>...</think> blocks only.\"\"\"
    if not text:
        return ""
    return re.sub(r"(?is)<think>.*?</think>", "", text).strip()
""".lstrip("\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()

    p = Path(args.file)
    s = p.read_text(encoding="utf-8")

    # A) Remove ALL existing _strip_think definitions (wherever they are)
    #    (We will re-insert exactly one clean copy.)
    s2 = re.sub(r"(?s)^\s*def _strip_think\([^\n]*\)\s*:\s*.*?(?=^\s*def |\Z)", "", s, flags=re.M)

    # B) Insert _strip_think after _safe_str (utilities area)
    m = re.search(r"(?m)^\s*def _safe_str\([^\n]*\)\s*:\s*\n(?:[ \t].*\n)+", s2)
    if not m:
        raise SystemExit("ERROR: could not find def _safe_str(...) block to anchor _strip_think insertion.")

    insert_at = m.end()
    # ensure exactly one blank line before insertion
    if not s2[insert_at:insert_at+2].startswith("\n\n"):
        s2 = s2[:insert_at] + "\n\n" + s2[insert_at:]
        insert_at += 2

    s2 = s2[:insert_at] + STRIP_THINK_DEF + "\n\n" + s2[insert_at:]

    # C) Fix the LLM try-block structure:
    #    Replace everything between "resp = _ollama_generate(prompt)" and "except Exception as e:"
    #    with a clean, properly-indented extraction/sanitization block.
    pattern = r"(?s)(?P<indent>^[ \t]*)resp\s*=\s*_ollama_generate\(prompt\)\s*\n.*?(?=^[ \t]*except\s+Exception\s+as\s+e\s*:)"
    mm = re.search(pattern, s2, flags=re.M)
    if not mm:
        raise SystemExit("ERROR: could not find the LLM block starting at resp = _ollama_generate(prompt).")

    indent = mm.group("indent")

    replacement = (
        f"{indent}resp = _ollama_generate(prompt)\n"
        f"{indent}llm_used = True\n\n"
        f"{indent}raw_text = resp.get(\"response\", \"\") or resp.get(\"message\", {{}}).get(\"content\", \"\")\n"
        f"{indent}narrative[\"text\"] = _strip_think(raw_text)\n\n"
        f"{indent}debug[\"ok\"] = True\n"
        f"{indent}debug[\"model\"] = os.environ.get(\"OLLAMA_MODEL\", \"\")\n"
        f"{indent}debug[\"provider\"] = os.environ.get(\"AURORA_LLM_PROVIDER\", \"\")\n"
        f"{indent}debug[\"url\"] = os.environ.get(\"OLLAMA_URL\", \"\")\n"
        f"{indent}debug[\"generated_at\"] = datetime.now().isoformat(timespec=\"seconds\")\n\n"
        f"{indent}# Optional: keep raw response only when explicitly enabled\n"
        f"{indent}if os.environ.get(\"AURORA_LLM_DEBUG_RAW\", \"\").strip() == \"1\":\n"
        f"{indent}    debug[\"raw\"] = resp\n"
    )

    s3 = re.sub(pattern, replacement, s2, flags=re.M)

    p.write_text(s3, encoding="utf-8")
    print("[OK] patched runner safely")

if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path
import re
import shutil

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
AURORA = REPO / "fantasyai" / "aurora"

TARGET_SCRIPTS = [
    "run_aurora_on_public_data_demo.py",
    "run_aurora_math_solvers_demo.py",
    "run_aurora_advanced_math_demo.py",
    "run_aurora_env_logistics_multimetric_demo.py",
    "run_aurora_forecast_regime_causal_demo.py",
]

def backup(p: Path) -> None:
    bak = p.with_suffix(p.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(p, bak)

def ensure_import(text: str, import_line: str, after_regex: str = r"^import .*|^from .*") -> str:
    if import_line in text:
        return text
    lines = text.splitlines()
    # insert after last import block at top
    idx = 0
    for i, line in enumerate(lines[:200]):
        if re.match(after_regex, line):
            idx = i + 1
    lines.insert(idx, import_line)
    return "\n".join(lines) + ("\n" if not text.endswith("\n") else "")

def inject_run_start(text: str, domain: str) -> str:
    # heuristic: after first "def main(" line, insert run creation after doc/print
    if "AuroraRun(" in text:
        return text

    m = re.search(r"^def main\(\).*:\s*$", text, flags=re.M)
    if not m:
        return text  # don't break unknown structure

    insert_at = m.end()
    # find next line after def main(): newline
    # We'll insert right after the function signature line.
    before = text[:insert_at]
    after = text[insert_at:]

    payload = f"""
    run = AuroraRun(domain="{domain}", tags={{"script":"{domain}"}})
"""
    return before + payload + after

def wrap_print_blocks(text: str) -> str:
    # Minimal safe approach:
    # If the script prints JSON dicts named like `res = {...}` then `print(res)`,
    # we attach run.add(...) next to print(res) by capturing simple patterns.
    # If patterns not found, we leave the file alone (no breaking).
    # We'll add a helper call `maybe_add(run, "block", obj)` and use it where possible.

    if "def _aurora_maybe_add(" in text:
        return text

    helper = """
def _aurora_maybe_add(run, name, obj):
    try:
        run.add(name, obj)
    except Exception:
        pass
"""
    # put helper near top (after imports)
    lines = text.splitlines()
    # place after imports and blank line
    insert_idx = 0
    for i, line in enumerate(lines[:250]):
        if line.strip() == "" and i > 0:
            insert_idx = i + 1
            break
    lines.insert(insert_idx, helper.strip("\n"))
    text = "\n".join(lines) + ("\n" if not text.endswith("\n") else "")

    # common pattern: print(json.dumps(obj, ...)) OR print(obj)
    # We'll only patch cases where variable name is obvious.
    patterns = [
        (r"^(\s*)print\((\w+)\)\s*$", r"\1_aurora_maybe_add(run, '\2', \2)\n\1print(\2)"),
        (r"^(\s*)print\(\s*json\.dumps\((\w+),", r"\1_aurora_maybe_add(run, '\2', \2)\n\1print(json.dumps(\2,"),
    ]
    for pat, rep in patterns:
        text = re.sub(pat, rep, text, flags=re.M)

    return text

def add_finalize(text: str) -> str:
    if "run.finalize()" in text:
        return text
    # try to insert before last return or at end of main()
    # easiest safe: append at end of file guarded by __main__ block presence.
    if "if __name__ == \"__main__\"" in text or "if __name__ == '__main__'" in text:
        # insert inside main() end is risky; append right before end of main() call by wrapping main() call area.
        text = re.sub(
            r"^(if __name__ == ['\"]__main__['\"]:\s*\n)(\s*)main\(\)\s*$",
            r"\1\2try:\n\2    paths = run.finalize()\n\2    print(f\"[AURORA ARTIFACTS] wrote -> {paths['run_dir']}\")\n\2except Exception as e:\n\2    print(f\"[AURORA ARTIFACTS] finalize failed: {e}\")\n\2main()",
            text,
            flags=re.M
        )
    return text

def main():
    # patch target scripts
    for fn in TARGET_SCRIPTS:
        p = SCRIPTS / fn
        if not p.exists():
            print(f"[patch] skip missing: {p}")
            continue
        backup(p)
        txt = p.read_text(encoding="utf-8")

        txt = ensure_import(txt, "from fantasyai.aurora.artifacts import AuroraRun")
        domain = fn.replace("run_", "").replace(".py", "")
        txt = inject_run_start(txt, domain=domain)
        txt = wrap_print_blocks(txt)
        txt = add_finalize(txt)

        p.write_text(txt, encoding="utf-8")
        print(f"[patch] patched: {p}")

    # ensure outputs folder exists
    out = REPO / "outputs" / "aurora_runs"
    out.mkdir(parents=True, exist_ok=True)
    print(f"[patch] ensured: {out}")

if __name__ == "__main__":
    main()

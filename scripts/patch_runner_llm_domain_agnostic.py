from __future__ import annotations
import argparse, re
from pathlib import Path

def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}")
    raise SystemExit(code)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        die(f"file not found: {p}")

    txt = p.read_text(encoding="utf-8")

    # -----------------------------
    # A) Make Aurora domain-agnostic (replace the old environment-only framing)
    # -----------------------------
    old_phrase = "You are Aurora, a quantitative environmental analyst."
    new_phrase = (
        "You are Aurora, a domain-agnostic quantitative analyst.\n"
        "You can analyze ANY tabular/time-series dataset (finance, logistics, healthcare, climate, operations, etc.).\n"
        "You must stay grounded in the provided engine outputs and avoid generic filler."
    )

    if old_phrase not in txt:
        die("Could not find the exact phrase to replace: 'You are Aurora, a quantitative environmental analyst.'")

    txt = txt.replace(old_phrase, new_phrase)

    # -----------------------------
    # B) Insert sanitizers after _strip_think (robust anchor)
    # -----------------------------
    anchor = r"def _strip_think\(s: str\) -> str:\s*[\s\S]*?return s\.strip\(\)\s*"
    m = re.search(anchor, txt)
    if not m:
        die("Could not anchor after _strip_think(...) to insert sanitizers.")

    insert_block = r"""

def _extract_json_object(s: str) -> str:
    """
    Extract the first top-level JSON object from a string.
    This is resilient to models that prepend/append text.
    """
    if not s:
        return ""
    # quick path
    s2 = s.strip()
    if s2.startswith("{") and s2.endswith("}"):
        return s2
    mm = re.search(r"(?s)\{.*\}", s2)
    return mm.group(0) if mm else ""

def _truncate_for_artifacts(obj, *, max_list_items: int = 24, max_str_len: int = 800):
    """
    Prevent giant arrays / dumps from bloating artifacts.
    Recursively truncates lists and long strings.
    """
    try:
        if obj is None:
            return None
        if isinstance(obj, str):
            return obj if len(obj) <= max_str_len else (obj[:max_str_len] + "…")
        if isinstance(obj, (int, float, bool)):
            return obj
        if isinstance(obj, list):
            out = []
            for i, v in enumerate(obj[:max_list_items]):
                out.append(_truncate_for_artifacts(v, max_list_items=max_list_items, max_str_len=max_str_len))
            if len(obj) > max_list_items:
                out.append(f"...truncated {len(obj) - max_list_items} items...")
            return out
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                out[str(k)] = _truncate_for_artifacts(v, max_list_items=max_list_items, max_str_len=max_str_len)
            return out
        # fallback
        s = str(obj)
        return s if len(s) <= max_str_len else (s[:max_str_len] + "…")
    except Exception:
        return obj
"""
    # only insert once
    if "_truncate_for_artifacts" not in txt:
        txt = txt[:m.end()] + insert_block + txt[m.end():]

    # -----------------------------
    # C) Tighten the prompt to discourage numeric arrays and enforce insight-note output
    #    We patch inside the prompt construction block.
    # -----------------------------
    # Replace the TASK/constraints paragraph with a stronger version.
    # We keep it conservative: only replace a known chunk, not the entire prompt.
    old_chunk = (
        "TASK: Produce an INSIGHT NOTE *only* about the provided data.\\n"
        "Return ONLY a single valid JSON object (no markdown, no extra text).\\n"
        "Do NOT include <think> tags or hidden reasoning.\\n\\n"
    )
    new_chunk = (
        "TASK: Produce an EXECUTIVE INSIGHT NOTE grounded ONLY in the provided data.\\n"
        "Return ONLY a single valid JSON object (no markdown, no extra text).\\n"
        "Do NOT include <think> tags, hidden reasoning, or any extra keys.\\n"
        "CRITICAL: Do NOT output large numeric arrays, simulations, or time-step bands.\\n"
        "If referencing quantities, cite ONLY a few key numbers (top 3–8) in plain text bullets.\\n"
        "Avoid generic capability statements. Provide concrete findings and what to do next.\\n\\n"
    )

    if old_chunk not in txt:
        die("Could not find the expected prompt TASK chunk to replace (prompt block drifted).")
    txt = txt.replace(old_chunk, new_chunk)

    # -----------------------------
    # D) Improve parsing + sanitize parsed JSON before writing artifacts
    # -----------------------------
    # Patch the parsing section to use _extract_json_object and sanitize.
    # We target a small, stable region around: parsed = json.loads(clean_text)
    find_region = r"parsed\s*=\s*None\s*[\s\S]*?narrative\.update\(\{"
    mr = re.search(find_region, txt)
    if not mr:
        die("Could not find parsing region to patch (looking for parsed=None ... narrative.update({ ).")

    # Replace the parsing logic block (between 'parsed = None' and just before narrative.update({')
    # with a safer version that extracts JSON object and sanitizes.
    start = mr.start()
    end = mr.end()

    # We'll locate the exact start at "parsed = None" line to keep indentation safe.
    s2 = txt[start:end]
    idx = s2.find("parsed = None")
    if idx == -1:
        die("Could not locate 'parsed = None' within the matched region.")
    # Keep everything up to 'parsed = None' line start indentation
    s2_prefix = s2[:idx]

    replacement_mid = s2_prefix + """parsed = None
                parse_error = None
                try:
                    blob = _extract_json_object(clean_text)
                    if not blob:
                        raise ValueError("no_json_object_detected")
                    parsed = json.loads(blob)
                except Exception as e:
                    parse_error = f"{type(e).__name__}:{e}"

                # sanitize to keep artifacts small even if model violates instructions
                if isinstance(parsed, dict):
                    parsed = _truncate_for_artifacts(parsed, max_list_items=24, max_str_len=800)
"""

    # Now we need to keep the remainder from the original region starting at narrative.update({
    # We can reconstruct by grabbing from the first occurrence of 'narrative.update({' in the original region.
    tail_idx = s2.find("narrative.update({")
    if tail_idx == -1:
        die("Could not locate 'narrative.update({' tail within parsing region.")
    replacement = replacement_mid + s2[tail_idx:]

    txt = txt[:start] + replacement + txt[end:]

    # -----------------------------
    # E) Keep raw_response out of artifacts by default
    #    (Your current code already does this via AURORA_LLM_DEBUG_RAW, good.)
    #    But ensure we never store giant fallback text.
    # -----------------------------
    # Tighten fallback_text to 1200 chars
    txt = re.sub(
        r'narrative\["fallback_text"\]\s*=\s*clean_text\[:4000\]',
        'narrative["fallback_text"] = clean_text[:1200]',
        txt
    )

    p.write_text(txt, encoding="utf-8")
    print("[OK] patched runner successfully")

if __name__ == "__main__":
    main()

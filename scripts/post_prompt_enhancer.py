import re, os, pathlib
from scripts.data_sources import sample_trend_keyword, get_fact_with_fallback

SAFE_SPICE = [
    "damn", "hell", "crap", "freakin’", "friggin’",
    "holy hell", "what the hell", "dammit", "BS", "absolute BS",
    "sh*t", "f***", "f***ing"
]

def _spice_line(line: str) -> str:
    if any(f" {w}, " in line or f" {w} " in line for w in SAFE_SPICE):
        return line
    interj = SAFE_SPICE[hash(line) % len(SAFE_SPICE)]
    return re.sub(r':\s*', f': {interj}, ', line, count=1)

def _edgify_dialogue(txt: str) -> tuple[str,int]:
    out, n = [], 0
    for line in txt.splitlines():
        if re.match(r'^\s*[🧍🐈🐻].*?:', line) and len(line) < 240:
            new = _spice_line(line)
            if new != line: n += 1
            line = new
        out.append(line)
    return "\n".join(out), n

def _dedupe_dialogue(txt: str) -> str:
    lines = txt.splitlines()
    out, prev = [], None
    for L in lines:
        if prev is not None and re.match(r'^\s*[🧍🐈🐻].*?:', L) and L == prev:
            continue
        out.append(L); prev = L
    return "\n".join(out)

def _retitle_segments(text: str, kw: str) -> str:
    def _repl(m):
        seg = m.group(1)
        return f"🎬 Segment {seg} – “{kw} – Pt. {seg}” (Selfie Stick POV)"
    return re.sub(
        r'^🎬\s*Segment\s*(\d+)\s*–\s*“[^”]+–\s*Pt\.\s*\1”\s*\(Selfie Stick POV\)\s*$',
        _repl, text, flags=re.M)

def _retitle_trend_line(text: str, kw: str) -> str:
    return re.sub(r'^(\s*)Trend:\s*.*$', rf"\1Trend: {kw}", text, flags=re.M)

def enhance_prompt(prompt_text: str) -> str:
    niche = os.getenv("NICHE","wildlife")
    kw = sample_trend_keyword(niche) or niche
    fact = get_fact_with_fallback(kw, niche=niche, max_chars=350)
    if fact:
        prompt_text += f"\n\n⸻\n🧠 Fact seed ({kw}): {fact}"
    prompt_text, count = _edgify_dialogue(prompt_text)
    prompt_text = _dedupe_dialogue(prompt_text)
    prompt_text = _retitle_segments(prompt_text, kw)
    prompt_text = _retitle_trend_line(prompt_text, kw)
    try: pathlib.Path("/tmp/fantasyai_trend_kw.txt").write_text(kw, encoding="utf-8")
    except Exception: pass
    prompt_text += f"\n\n🔧 enhancer: applied={count} trend='{kw}'"
    return prompt_text

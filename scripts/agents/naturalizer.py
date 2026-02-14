import os, re, random, sys
from scripts.pipeline import HailoInference

NO_HUMAN_TERMS = ("crowd","people","person","man","woman","kid","child","human")
FILLERS = ["uh","okay","right","look","so","yeah","honestly","alright"]
CONTRACTIONS = {
    r"\bdo not\b":"don't", r"\bdoes not\b":"doesn't", r"\bare not\b":"aren't",
    r"\bit is\b":"it's", r"\bthat is\b":"that's", r"\bthere is\b":"there's",
    r"\bi am\b":"i'm", r"\bwe are\b":"we're", r"\byou are\b":"you're",
    r"\bgoing to\b":"gonna", r"\bgot to\b":"gotta", r"\bkind of\b":"kinda",
    r"\bsort of\b":"sorta", r"\bwant to\b":"wanna"
}

def _contractions(s:str)->str:
    out=s
    for pat,rep in CONTRACTIONS.items():
        out = re.sub(pat, rep, out, flags=re.I)
    return out

def _strip_topic_spam(line:str, topic:str)->str:
    t = (topic or "").strip()
    if not t: return line
    return re.sub(re.escape(t), "that", line, flags=re.I)

def _ban_humans(line:str)->str:
    out=line
    for w in NO_HUMAN_TERMS:
        out = re.sub(rf"\b{re.escape(w)}\b", "crew", out, flags=re.I)
    return out

def _keep_animals_only(line:str)->str:
    return re.sub(r"\b(actor|actress|model|extra)\b", "figure", line, flags=re.I)

def _inject_filler(line:str)->str:
    if random.random() < 0.35:
        return f"{random.choice(FILLERS).capitalize()}, {line}"
    return line

def _extract_dialogue_block(text:str):
    m = re.search(r"(?s)(^|\n)(🎤\s*Dialogue\s*\(\d+\s*s\)\s*\n)(.*?)(\n\n(?:🎛️|🎭|🎥|📍|📰)|\Z)", text)
    if not m: return None
    start, head, body, tail = m.start(3), m.group(2), m.group(3), m.group(4)
    return (start, head, body, tail)

def _ensure_vibe_hyperreal(text:str)->str:
    if re.search(r"hyper[- ]?real", text, flags=re.I):
        return text
    return re.sub(r"(🎭\s*Vibe\s*\n)(.*)", r"\1\2\nHyper-realistic scenery; photoreal textures; no humans.", text, count=1, flags=re.I|re.S)

def _rule_rewrite(body:str, topic:str)->str:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    out=[]
    for ln in lines:
        if ": " not in ln:
            out.append(ln); continue
        who, text = ln.split(": ", 1)
        text = text.strip()
        text = _strip_topic_spam(text, topic)
        text = _contractions(text)
        text = _ban_humans(text)
        text = _keep_animals_only(text)
        text = _inject_filler(text)
        if len(text) > 120:
            text = re.sub(r",\s*", ", ", text); text = text[:120].rstrip() + "…"
        out.append(f"{who}: {text}")
    uniq=[]; seen=set()
    for ln in out:
        key=re.sub(r"[^a-z]+","", ln.lower())
        if key not in seen:
            seen.add(key); uniq.append(ln)
    return "\n".join(uniq)

def rewrite_dialogue(prompt_text:str, topic:str, use_ollama:bool|None=None)->str:
    use_ollama = use_ollama if use_ollama is not None else (os.getenv("MICRO_OLLAMA","0") in ("1","true","yes"))
    blk = _extract_dialogue_block(prompt_text)
    if not blk:
        return _ensure_vibe_hyperreal(prompt_text)
    start, head, body, tail = blk
    rewritten = _rule_rewrite(body, topic)
    if use_ollama:
        try:
            hi = HailoInference()
            sys_prompt = (
              "Rewrite the following dialogue lines into casual, natural vlog banter.\n"
              "Constraints: no human mentions; keep speakers Yeti/Kitty/Bear; short lines; natural contractions.\n"
              "Return ONLY the lines, speaker tags preserved."
            )
            ask = sys_prompt + "\n\n" + rewritten
            resp = hi.generate_text(ask, max_tokens=220)
            if resp and ":" in resp:
                rewritten = resp.strip()
        except Exception:
            pass
    new_text = prompt_text[:start] + rewritten + tail + prompt_text[start+len(body):]
    return _ensure_vibe_hyperreal(new_text)

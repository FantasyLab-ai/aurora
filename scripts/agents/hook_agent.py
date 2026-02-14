import re, random

def make_hook(topic:str, characters:list[str]|None=None)->str:
    t=(topic or "").strip()
    who = ", ".join(characters or [])
    bank = [
        f"We’re rolling on: {t}.",
        f"Did we just stumble into {t}?",
        f"{who} caught this live: {t}.",
        f"{t}. No second take.",
        f"Hold up—this is about {t}.",
    ]
    return random.choice(bank)

def inject_hook(prompt_text:str, hook:str)->str:
    # Insert as first line of Dialogue
    m = re.search(r"(?s)(Dialogue\s*\(\d+\s*s\)\s*)(.+?)((?:\n\n(?:Artifacts|Vibe|Camera|Environment)\b)|\Z)", prompt_text)
    if not m: return prompt_text
    head, body, tail = m.group(1), m.group(2), m.group(3)
    lines = [ln for ln in body.splitlines()]
    lines.insert(0, f"🪝 Hook: {hook}")
    return prompt_text[:m.start()] + head + "\n".join(lines) + tail + prompt_text[m.end():]

import json, os, random
from pathlib import Path

DATA_DIR = os.path.expanduser(os.getenv("DATA_DIR", "~/FantasyAI/data"))
os.makedirs(DATA_DIR, exist_ok=True)
RECENT_TEAMS_PATH = Path(DATA_DIR) / "recent_teams.json"

# Character catalog with A/B variants so we can do doubles
CHARACTERS = {
    "Yeti A": "🧍‍♂️ Yeti A (deadpan cam-op): 7' pale-gray fur; hi-viz vest; careful thumbs.",
    "Yeti B": "🧍‍♂️ Yeti B (dry humor, steadier): 7' ash fur; patched vest; wide stance.",
    "Bear A": "🐻 Bear A (logistics, hungry): massive brown; tight neon pack; patient until snacks end.",
    "Bear B": "🐻 Bear B (methodical, gentle): deep brown; harnessed mic; notices details.",
    "Kitty A": "🐈 Kitty A (ops, razor sarcasm): upright tabby; padded vest; ears flick at micro-sounds.",
    "Kitty B": "🐈 Kitty B (sharp, terse): charcoal tabby; snug vest; laser focus.",
}

DUO_BUCKETS = {
    "awe": [("Yeti A","Bear A"), ("Yeti B","Bear B"), ("Bear A","Bear B")],
    "docu": [("Yeti A","Kitty A"), ("Yeti B","Bear A"), ("Bear B","Kitty B")],
    "tense": [("Yeti A","Kitty B"), ("Yeti B","Bear A"), ("Bear A","Bear A")],
    "comedy": [("Yeti A","Kitty A"), ("Kitty A","Kitty B"), ("Yeti A","Yeti B")],
    "cozy": [("Yeti B","Bear B"), ("Kitty A","Bear B"), ("Kitty A","Kitty B")],
    "surreal": [("Yeti A","Yeti B"), ("Bear A","Bear B"), ("Kitty A","Kitty B")],
    "light": [("Yeti A","Bear B"), ("Yeti B","Kitty A"), ("Bear A","Kitty B")],
}

def _load_recent(max_keep=10):
    try:
        if RECENT_TEAMS_PATH.exists():
            data = json.loads(RECENT_TEAMS_PATH.read_text(encoding="utf-8"))
            return data[-max_keep:]
    except Exception:
        pass
    return []

def _save_recent(team):
    recent = _load_recent()
    recent.append(team)
    try:
        RECENT_TEAMS_PATH.write_text(json.dumps(recent[-10:]), encoding="utf-8")
    except Exception:
        pass

def pick_team_for_tone(tone: str) -> list[str]:
    tone = (tone or "docu").lower()
    bucket = DUO_BUCKETS.get(tone, DUO_BUCKETS["docu"])
    # Avoid repeating the last team when possible
    recent = _load_recent()
    choices = [t for t in bucket if t not in recent] or bucket
    team = random.choice(choices)
    _save_recent(team)
    return list(team)

def expand_descriptions(characters: list[str]) -> dict:
    return {c: {"short": CHARACTERS.get(c, c)} for c in characters}

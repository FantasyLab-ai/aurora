import os, json
from pathlib import Path

_CFG = None
def _load():
    global _CFG
    if _CFG is not None: return _CFG
    path = Path(os.getenv("CFG_PATH", "~/FantasyAI/config.json")).expanduser()
    if path.exists():
        try: _CFG = json.loads(path.read_text(encoding="utf-8"))
        except Exception: _CFG = {}
    else:
        _CFG = {}
    return _CFG

def cfg_get(key, default=None, cast=None):
    # env overrides config.json
    v = os.getenv(key)
    if v is None:
        v = _load().get(key, default)
    if cast:
        try: return cast(v)
        except Exception: return default
    return v if v is not None else default

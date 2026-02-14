from __future__ import annotations
from pathlib import Path
import importlib.util, inspect, types, os

BACKUP_PATH = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts/pipeline_final.py.bak_fixed")

def _load_backup():
    spec = importlib.util.spec_from_file_location("pipeline_final_bak", str(BACKUP_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import backup at {BACKUP_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_bak = _load_backup()
_bak_src = BACKUP_PATH.read_text(encoding="utf-8")

def _first_callable(mod: types.ModuleType, candidates: list[str]) -> callable | None:
    for name in candidates:
        if hasattr(mod, name) and callable(getattr(mod, name)):
            return getattr(mod, name)
    return None

def _find_def_name_in_source(substr: str) -> str | None:
    # best-effort: find a def whose name contains substr
    import re
    m = re.search(rf'(?m)^\s*def\s+(\w*{substr}\w*)\s*\(', _bak_src, re.I)
    return m.group(1) if m else None

def _maybe_class_method(mod: types.ModuleType, class_substr: str, meth_name: str = "generate"):
    # e.g., class VeoCDP(...): def generate(prompt_text): ...
    for name, obj in vars(mod).items():
        if isinstance(obj, type) and class_substr.lower() in name.lower():
            if hasattr(obj, meth_name) and callable(getattr(obj, meth_name)):
                try:
                    inst = obj()  # assume no-arg ctor
                    return lambda prompt: getattr(inst, meth_name)(prompt)
                except Exception:
                    pass
    return None

# Heuristics for CDP
_cdp = (
    _first_callable(_bak, [
        "generate_veo_video_cdp","veo_cdp_generate","cdp_generate_video",
        "generate_video_cdp","start_cdp_flow","run_cdp","generate_via_cdp"
    ])
    or (lambda nm=_find_def_name_in_source("cdp"): getattr(_bak, nm) if nm and hasattr(_bak, nm) else None)()
    or _maybe_class_method(_bak, "cdp", "generate")
)

# Heuristics for Selenium
_sel = (
    _first_callable(_bak, [
        "generate_veo_video_selenium","veo_selenium_generate","selenium_generate_video",
        "generate_video_selenium","start_selenium_flow","run_selenium","generate_via_selenium"
    ])
    or (lambda nm=_find_def_name_in_source("selenium"): getattr(_bak, nm) if nm and hasattr(_bak, nm) else None)()
    or _maybe_class_method(_bak, "selenium", "generate")
)

def _must(fn, label):
    if not callable(fn):
        raise RuntimeError(f"{label} not found in backup. Open {BACKUP_PATH} and confirm function/class names.")
    return fn

# Public wrappers your pipeline expects:
def generate_veo_video_cdp(prompt_text: str) -> str:
    fn = _must(_cdp, "CDP generator")
    return fn(prompt_text)

def generate_veo_video_selenium(prompt_text: str) -> str:
    fn = _sel
    if not callable(fn):
        raise RuntimeError("Selenium generator not present in backup.")
    return fn(prompt_text)

def generate_veo_video(prompt_text: str) -> str:
    # Keep your env switch: prefer CDP unless explicitly SELENIUM_OK=1
    truthy = lambda v: str(v).lower() in ("1","true","yes","y")
    if truthy(os.getenv("USE_CHROME_CDP","1")):
        return generate_veo_video_cdp(prompt_text)
    if truthy(os.getenv("SELENIUM_OK","0")):
        return generate_veo_video_selenium(prompt_text)
    # fallback: CDP
    return generate_veo_video_cdp(prompt_text)

from __future__ import annotations
from pathlib import Path
import types, re, os

BACKUP_PATH = Path(r"/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts/pipeline_final.py.bak_fixed")

def _load_backup_safely():
    if not BACKUP_PATH.exists():
        raise RuntimeError(f"Backup not found: {BACKUP_PATH}")

    src = BACKUP_PATH.read_text(encoding="utf-8")

    # 1) Neutralize any __main__ runners so nothing fires on import
    src = re.sub(r'(?m)^\s*if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:\s*.*\Z', "# __main__ removed by shim\n", src, flags=re.S)

    # 2) Prepend shims for missing types / flags that have bitten us
    pre = [
        "from __future__ import annotations",
        "# --- shims injected by loader ---",
        "SELENIUM_IMPORT_ERR = None",
        "try:\n    _CDPConn\nexcept NameError:\n    class _CDPConn: pass",
        "",
    ]
    src = "\n".join(pre) + "\n" + src

    # 3) Exec into an isolated module object (no regular import, so relative imports don’t run)
    mod = types.ModuleType("pipeline_final_bak_dyn")
    mod.__dict__["__file__"] = str(BACKUP_PATH)
    exec(compile(src, str(BACKUP_PATH), "exec"), mod.__dict__)
    return mod

_bak = _load_backup_safely()

def _pick_fn(mod, want: str):
    """
    Try a sequence of strategies to find a generator:
    - exact function names
    - any function name containing the key
    - a class with name containing the key and method generate/run
    Returns a callable(prompt_text)->str
    """
    import inspect

    # 1) direct candidates
    direct = {
        "cdp": [
            "generate_veo_video_cdp","veo_cdp_generate","cdp_generate_video",
            "generate_video_cdp","start_cdp_flow","run_cdp","generate_via_cdp"
        ],
        "selenium": [
            "generate_veo_video_selenium","veo_selenium_generate","selenium_generate_video",
            "generate_video_selenium","start_selenium_flow","run_selenium","generate_via_selenium"
        ],
    }[want]

    for name in direct:
        fn = getattr(_bak, name, None)
        if callable(fn):
            return fn

    # 2) heuristic: any function containing key
    for name, obj in list(vars(_bak).items()):
        if callable(obj) and want in name.lower():
            try:
                sig = inspect.signature(obj)
                if "prompt" in ",".join(sig.parameters.keys()).lower() or len(sig.parameters) >= 1:
                    return obj
            except Exception:
                return obj

    # 3) class with key substring and generate/run
    for cname, cls in list(vars(_bak).items()):
        if isinstance(cls, type) and want in cname.lower():
            for meth in ("generate","run","start"):
                if hasattr(cls, meth) and callable(getattr(cls, meth)):
                    try:
                        inst = cls()
                        return lambda prompt: getattr(inst, meth)(prompt)
                    except Exception:
                        # try as @staticmethod/@classmethod
                        m = getattr(cls, meth, None)
                        if callable(m):
                            return m

    return None

_cdp = _pick_fn(_bak, "cdp")
_sel = _pick_fn(_bak, "selenium")

def generate_veo_video_cdp(prompt_text: str) -> str:
    if not callable(_cdp):
        raise RuntimeError("CDP generator not found in backup. Please open the backup and tell me the function/class name.")
    return _cdp(prompt_text)

def generate_veo_video_selenium(prompt_text: str) -> str:
    if not callable(_sel):
        raise RuntimeError("Selenium generator not found in backup.")
    return _sel(prompt_text)

def generate_veo_video(prompt_text: str) -> str:
    thr = lambda v: str(v).lower() in ("1","true","yes","y")
    if thr(os.getenv("USE_CHROME_CDP","1")):
        return generate_veo_video_cdp(prompt_text)
    if thr(os.getenv("SELENIUM_OK","0")):
        return generate_veo_video_selenium(prompt_text)
    return generate_veo_video_cdp(prompt_text)

import os, sys, logging, inspect, types
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("FantasyAI")

ROOT = "/mnt/c/Users/bgrut/Desktop/FantasyAI"
sys.path[:0] = [ROOT, f"{ROOT}/scripts", f"{ROOT}/scripts/agents"]

# --- Imports (best effort) ---
import importlib
pipeline_mod = importlib.import_module("scripts.pipeline_final")
enh = importlib.import_module("scripts.post_prompt_enhancer")
mw  = importlib.import_module("scripts.metrics_writer")
ssa = importlib.import_module("scripts.social_source_agent")
ds  = importlib.import_module("scripts.data_sources")

# --- Social-first trend keyword monkeypatch (safe fallback to original) ---
_orig_sample_trend_keyword = getattr(ds, "sample_trend_keyword", None)
def _sample_trend_keyword_social_first(niche: str):
    try:
        picks = ssa.harvest_social_keywords(niche=niche or "wildlife", max_out=15)
        if picks:
            return picks[0]
    except Exception as e:
        log.warning("social source failed: %s", e)
    if callable(_orig_sample_trend_keyword):
        return _orig_sample_trend_keyword(niche)
    return (niche or "wildlife")
if callable(_orig_sample_trend_keyword):
    ds.sample_trend_keyword = _sample_trend_keyword_social_first

# --- Helper: apply enhancer + metrics to a prompt string ---
def _enhance(text: str) -> str:
    try:
        return enh.enhance_prompt(text)
    except Exception as e:
        log.warning("Enhancer failed; using raw prompt: %s", e)
        return text

def _write_metrics(prompt_text: str, video_path: str):
    try:
        mw.write_metrics({
            "niche": os.getenv("NICHE",""),
            "video_path": video_path or "",
            "score": "",                 # fill later if your scorer exposes it
            "prompt_text": prompt_text,  # metrics_writer derives pair/kw/len/hash
        })
    except Exception as e:
        log.warning("metrics write failed: %s", e)

# --- Find FantasyAIPipeline class ---
Pipe = getattr(pipeline_mod, "FantasyAIPipeline", None)
if Pipe is None or not isinstance(Pipe, type):
    raise SystemExit("FantasyAIPipeline class not found in pipeline_final.py")

# --- Try to find a generation method to wrap ---
def _choose_generation_method(cls):
    prefer_exact = ["generate_veo_video", "generate_video", "render_video", "generate"]
    all_methods = {name: fn for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction)}
    for name in prefer_exact:
        if name in all_methods:
            return name, all_methods[name]
    # fuzzy match
    for name, fn in all_methods.items():
        low = name.lower()
        if any(k in low for k in ["veo","video","render","generate","produce"]):
            return name, fn
    return None, None

target_name, target_fn = _choose_generation_method(Pipe)

def _call_with_new_prompt(self, fn, new_prompt, *args, **kwargs):
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    # kwarg slots
    if "prompt_text" in params:
        kwargs["prompt_text"] = new_prompt
        return fn(self, *args, **kwargs)
    if "prompt" in params:
        kwargs["prompt"] = new_prompt
        return fn(self, *args, **kwargs)
    # positional (assume first arg is prompt if it is a str at runtime)
    if args:
        new_args = list(args)
        new_args[0] = new_prompt
        return fn(self, *new_args, **kwargs)
    # no obvious slot: just call as-is (last resort)
    return fn(self, *args, **kwargs)

wrapped_gen = False
if target_fn:
    orig = target_fn
    def _gen_wrap(self, *a, **k):
        # Try to sniff the original prompt to enhance it; fallback to first positional/kw
        prompt = k.get("prompt_text") or k.get("prompt")
        if prompt is None and a:
            prompt = a[0] if isinstance(a[0], str) else None
        if not isinstance(prompt, str):
            log.warning("Could not locate prompt argument on %s(); enhancer will be skipped for this call.", target_name)
            result_path = orig(self, *a, **k)
            _write_metrics("(unknown prompt)", result_path if isinstance(result_path, str) else "")
            return result_path
        new_prompt = _enhance(prompt)
        result_path = _call_with_new_prompt(self, orig, new_prompt, *a, **k)
        _write_metrics(new_prompt, result_path if isinstance(result_path, str) else "")
        return result_path

    setattr(Pipe, target_name, _gen_wrap)
    wrapped_gen = True
    log.info("✅ Patched %s.%s with enhancer + metrics", Pipe.__name__, target_name)

# --- If we couldn't wrap a generator, wrap the prompt generator instead ---
if not wrapped_gen:
    # try PromptGeneratorStructured.generate / build
    PG = getattr(pipeline_mod, "PromptGeneratorStructured", None)
    if isinstance(PG, type):
        for meth_name in ("generate", "build", "compose"):
            fn = getattr(PG, meth_name, None)
            if callable(fn):
                orig = fn
                def _pg_wrap(self, *a, **k):
                    text = orig(self, *a, **k)
                    if isinstance(text, str):
                        return _enhance(text)
                    return text
                setattr(PG, meth_name, _pg_wrap)
                log.info("✅ Patched %s.%s (prompt stage) with enhancer", PG.__name__, meth_name)
                break
    if not wrapped_gen:
        log.warning("No generation method found; enhancer attached to prompt stage only (metrics will log after video if your pipeline writes them).")

# --- Run the pipeline ---
if __name__ == "__main__":
    pipeline_mod.FantasyAIPipeline().run()

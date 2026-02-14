from __future__ import annotations
import os, time, concurrent.futures, random, traceback
from scripts.generator_playwright import generate_veo_video  # your existing entry
from scripts.video_quality import quality_ok
from scripts.metrics import log_run

def run_one(topic: str, prompt: str):
    t0 = time.time()
    try:
        out = generate_veo_video(prompt)  # assumes it targets active Flow tab; your CDP code handles it
        ok, why = quality_ok(out)
        log_run(topic, prompt, bool(ok), time.time()-t0, out if ok else "")
        return (out if ok else ""), ok, why
    except Exception as e:
        traceback.print_exc()
        log_run(topic, prompt, False, time.time()-t0, "")
        return "", False, str(e)

def concurrent_run(topic_prompts: list[tuple[str,str]], max_workers: int = 2):
    # If CDP/Edge isn’t robust enough, reduce to 1—don’t crash
    if max_workers < 1: max_workers = 1
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(run_one, t, p) for (t,p) in topic_prompts]
        for f in concurrent.futures.as_completed(futs):
            results.append(f.result())
    return results

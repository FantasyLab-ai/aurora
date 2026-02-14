# ~/FantasyAI/control/orchestrator/decide_and_run.py
from __future__ import annotations
from typing import List, Dict, Any
import os, json, subprocess, shlex, time
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from scripts.trends_hub import get_trends
from scripts.prompt_builder import build_niche_prompt
from scripts.engines import run_pipeline

router = APIRouter()

def tail_text(txt: str, n: int = 60) -> str:
    if not txt:
        return ""
    lines = txt.splitlines()
    return "\n".join(lines[-n:])

@router.post("/cmd/decide-and-run")
def decide_and_run(
    n: int = Body(1),
    niche: str = Body("Yeti & Bigfoot vloggers (hyper-real, explicit)"),
    trend_hint: str = Body(""),

    # New optional controls (safe defaults)
    scene: str = Body(""),
    allow_explicit: bool = Body(True),
    duration_s: int = Body(8),
    orientation: str = Body("9:16"),
    engine: str = Body("veo"),   # future: "sora"
) -> JSONResponse:
    t0 = time.time()
    try:
        topics: List[str] = []
        if trend_hint.strip():
            topics = [trend_hint.strip()]
        else:
            # fall back to live trends
            topics = get_trends(limit=max(3, n))

        picked = topics[:max(1, n)]
        results: List[Dict[str, Any]] = []

        for topic in picked:
            prompt = build_niche_prompt(
                niche=niche,
                trend=topic,
                scene=scene or None,
                allow_explicit=allow_explicit
            )
            # Persist last prompt for debugging
            last_prompt_file = os.path.expanduser("~/Desktop/FantasyAI/last_prompt.txt")
            os.makedirs(os.path.dirname(last_prompt_file), exist_ok=True)
            with open(last_prompt_file, "w", encoding="utf-8") as f:
                f.write(prompt)

            run_out = run_pipeline(
                prompt=prompt,
                engine=engine,
                duration_s=duration_s,
                orientation=orientation,
            )

            results.append({
                "topic": topic,
                "prompt_file": last_prompt_file,
                "output_path": run_out.get("output_path",""),
                "run_ok": run_out.get("ok", False),
                "stdout_tail": tail_text(run_out.get("stdout","")),
                "stderr_tail": tail_text(run_out.get("stderr","")),
            })

        return JSONResponse({
            "ok": all(r.get("run_ok") for r in results),
            "niche": niche,
            "picked_topics": picked,
            "results": results,
            "elapsed_s": round(time.time() - t0, 2)
        })
    except Exception as e:
        return JSONResponse({
            "ok": False,
            "error": f"{type(e).__name__}: {e}"
        }, status_code=500)

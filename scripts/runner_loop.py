from __future__ import annotations
import os, time, random, traceback, hashlib
from scripts.topic_aggregator import get_topics
from scripts.prompt_render import render_candidates
from scripts.bandits import pick_ucb1
from scripts.metrics import log_run
from scripts.generator_playwright import generate_veo_video
from scripts.video_quality import quality_ok
from scripts.postprocess import make_thumbnail
from scripts.captions import make_captions, burn_in
from scripts.alerts import notify
from scripts.novelty import novelty_score
from scripts.schedule_core import schedule_everywhere

OUTPUTS_DIR = os.getenv("OUTPUTS_DIR", "/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs")

def pick_prompt(topic: str):
    cands = render_candidates(topic, k=3, overrides={"duration_s": random.choice([9,10,11,12])})
    # weight by novelty: duplicate high-novelty candidates to bias bandit exploration
    nov = novelty_score(topic)
    if nov > 0.66 and len(cands) >= 2:
        cands.append(random.choice(cands))
    return pick_ucb1(cands)

def run_once():
    topics = get_topics(max_items=6)
    topic = topics[0]
    prompt = pick_prompt(topic)

    print(f"\n📝 Topic: {topic}\n✨ Prompt: {prompt}\n")
    t0 = time.time()
    try:
        video = generate_veo_video(prompt)
        ok, why = quality_ok(video)
        if not ok:
            notify("Quality gate failed", "warning", {"why": why, "video": video})
        dur = time.time() - t0
        log_run(topic, prompt, bool(ok), dur, video if ok else "")
        base = os.path.splitext(video)[0]

        # Thumbnail
        thumb = base + "_thumb.png"
        try: make_thumbnail(video, topic, thumb)
        except Exception: pass

        # Captions (best effort)
        srt = base + ".srt"
        srt_path = make_captions(video, srt)  # empty if whisper not installed
        if srt_path:
            burned = base + "_cap.mp4"
            burn_in(video, srt_path, burned)  # optional; ignore output if ""

        if ok:
            # Schedule to platforms
            title = (topic[:80] or "Short")
            description = f"{topic}\n#FantasyAI"
            tags = ["wildlife","ai","shorts"]
            sched = schedule_everywhere(video, title, description, tags, topic)
            print("📅 Scheduled:", sched)
            notify("Scheduled OK", "info", {"topic": topic, **sched})

        print(f"✅ Done in {dur:.1f}s → {video}")
    except Exception as e:
        notify("Generation error", "error", {"err": str(e)})
        traceback.print_exc()

if __name__ == "__main__":
    interval = int(os.getenv("GEN_INTERVAL_SEC", "900"))
    while True:
        run_once()
        time.sleep(interval)

from __future__ import annotations
import os, time, traceback
from scripts.generator_playwright import generate_veo_video
from scripts.prompt_agent import get_topics, candidate_prompts
from scripts.metrics import thompson_pick, log_run

def main():
    topics = get_topics()
    topic = topics[0]
    cands = candidate_prompts(topic)
    prompt = thompson_pick(cands)

    print(f"📝 Topic: {topic}")
    print(f"✨ Prompt: {prompt}")

    t0 = time.time()
    try:
        video_path = generate_veo_video(prompt)
        dur = time.time() - t0
        print(f"✅ Video generated → {video_path}")
        log_run(topic, prompt, True, dur, video_path)
    except Exception as e:
        dur = time.time() - t0
        print("❌ Generation failed:", e)
        traceback.print_exc()
        log_run(topic, prompt, False, dur, "")

if __name__ == "__main__":
    main()

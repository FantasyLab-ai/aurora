import pathlib, json, logging
from scripts.metrics import extract_metrics, log_metrics_and_reward
from scripts.db import upsert_prompt_run
from scripts.agents.caption_agent import generate_caption_hashtags
from scripts.agents.hashtag_agent import blend as blend_hashtags
from scripts.subtitles import build_srt_from_prompt, burn_in
from scripts.thumbnailer import make_thumbnail
from scripts.aspectify import make_variants
from tools.build_dashboard import build as build_dashboard

def postprocess_after_render(video_path, trend, team, prompt_text):
    p=pathlib.Path(video_path)
    metrics=extract_metrics(str(p))
    row=log_metrics_and_reward("data/prompt_history.csv", trend, team or [], prompt_text, metrics)
    caption, tags = generate_caption_hashtags(trend, prompt_text, team or [], metrics)
    tags = blend_hashtags(tags, trend)
    p.with_suffix(".caption.txt").write_text(caption,encoding="utf-8")
    p.with_suffix(".hashtags.txt").write_text(" ".join(tags),encoding="utf-8")
    p.with_suffix(".metrics.json").write_text(json.dumps({"metrics":metrics,"reward":row["reward"]},indent=2),encoding="utf-8")
    # Subtitles
    try:
        srt = build_srt_from_prompt(prompt_text, str(p.with_suffix(".srt")))
        if srt:
            burned = burn_in(str(p), srt)
            if burned:
                p = pathlib.Path(burned)
    except Exception as e:
        logging.warning("subtitle gen failed: %s", e)
    # Thumbnail
    try:
        make_thumbnail(str(p), title=trend)
    except Exception as e:
        logging.warning("thumbnail gen failed: %s", e)
    # Multi-aspect
    try:
        make_variants(str(p))
    except Exception as e:
        logging.warning("aspect variants failed: %s", e)
    try:
        build_dashboard()
    except Exception as e:
        logging.warning('dashboard build failed: %s', e)
    return True

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import os
import requests  # used for Ollama classification

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def run_yt_search(query: str, limit: int = 25) -> list[dict]:
    """
    Use yt-dlp's 'ytsearch' mode to get public metadata for Sora-style AI videos.
    No API key required.
    """
    search_expr = f"ytsearch{limit}:{query}"
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--skip-download",
        search_expr,
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    videos: list[dict] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            videos.append(obj)
        except Exception:
            continue

    return videos


def is_meme_candidate(video: dict) -> bool:
    """
    Filter for comedy / unhinged / viral-style Sora clips,
    and aggressively drop tutorials / explainers / prompt guides.
    """
    title = (video.get("title") or "").lower()
    desc = (video.get("description") or "").lower()

    # Hard filter OUT anything that looks like a tutorial / explainer / prompt guide.
    tutorial_keywords = [
        "how to use sora",
        "how to make",
        "how i made",
        "tutorial",
        "guide",
        "step by step",
        "prompt guide",
        "prompting guide",
        "prompt ideas",
        "best sora prompts",
        "top sora prompts",
        "learn sora",
        "explained",
        "explainer",
        "review",
        "walkthrough",
        "course",
        "masterclass",
        "setup",
        "install",
        "beta update",
        "update review",
        "sora settings",
        "sora interface",
    ]
    if any(kw in title or kw in desc for kw in tutorial_keywords):
        return False

    # Positive meme / chaos keywords.
    meme_keywords = [
        "funny",
        "hilarious",
        "meme",
        "memes",
        "wtf",
        "cursed",
        "unhinged",
        "chaos",
        "goes wrong",
        "fails",
        "compilation",
        "eslop",
        "shitpost",
        "parody",
        "crack",
        "insane",
        "no way this is real",
        "ai video meme",
    ]
    if any(kw in title or kw in desc for kw in meme_keywords):
        return True

    # If we get here, it's ambiguous. Optionally ask DeepSeek to classify.
    try:
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
        model = os.getenv("OLLAMA_MODEL_CLASSIFY", "deepseek-r1:8b")

        system_msg = (
            "You classify AI video metadata into two buckets: 'meme' vs 'tutorial'. "
            "Return JSON only, with a single field 'label' that is either 'meme' or 'tutorial'. "
            "Treat bizarre, cinematic, funny, cursed, or unhinged content as 'meme'. "
            "Treat prompt guides, how-to videos, technical explainers as 'tutorial'."
        )

        user_msg = textwrap.dedent(
            f"""
            TITLE: {video.get('title') or ''}
            DESCRIPTION: {video.get('description') or ''}

            Return JSON like:
            {{"label": "meme"}}
            or
            {{"label": "tutorial"}}
            """
        ).strip()

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
        }

        resp = requests.post(url, json=payload, timeout=45)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "").strip()
        obj = json.loads(content)
        label = (obj.get("label") or "").lower()
        if label == "tutorial":
            return False
        if label == "meme":
            return True
    except Exception:
        # If classification fails, fall back to keeping the candidate (we'll curate later).
        pass

    # Default: keep it, but we will still curate seeds manually if needed.
    return True


def summarize_hit_with_ollama(video: dict) -> dict:
    """
    Send a single video (title, description, stats) to DeepSeek via Ollama and
    get back a structured summary of its 'Sora meme energy'.
    """
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
    model = os.getenv("OLLAMA_MODEL_TREND", "deepseek-r1:8b")

    title = video.get("title") or ""
    desc = video.get("description") or ""
    views = video.get("view_count") or 0
    uploader = video.get("uploader") or ""
    webpage_url = video.get("webpage_url") or ""

    system_msg = (
        "You are analyzing viral, funny, or unhinged AI video content (often created with Sora). "
        "You see the title, description and basic stats of a video. "
        "Your job is to return a VERY SHORT JSON object capturing the core visual theme, "
        "opening hook, camera vibe, emotional tone, and weirdness level (1-5). "
        "Focus on how a director would describe the shot concept for a 10-second meme banger."
    )

    user_msg = textwrap.dedent(
        f"""
        VIDEO METADATA
        --------------
        Title: {title}
        Description: {desc}
        Uploader: {uploader}
        Views: {views}
        URL: {webpage_url}

        Return JSON only, like:
        {{
          "theme_seed": "...",
          "opening_hook": "...",
          "camera_vibe": "...",
          "tone": "...",
          "weirdness": 1-5
        }}
        """
    ).strip()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "").strip()
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = {"raw": content}

        parsed["source_title"] = title
        parsed["source_url"] = webpage_url
        parsed["views"] = views
        return parsed
    except Exception as e:
        return {
            "error": str(e),
            "source_title": title,
            "source_url": webpage_url,
            "views": views,
        }


def main() -> None:
    # Query set skewed toward weird / funny Sora-style content, not tutorials.
    queries = [
        "sora ai funny meme 9:16",
        "sora ai insane video compilation",
        "hyper realistic ai meme video sora",
        "sora ai cursed video",
        "sora ai wtf 10 seconds",
        "ai video meme hyper realistic sora",
    ]

    all_videos: list[dict] = []
    for q in queries:
        vids = run_yt_search(q, limit=25)
        all_videos.extend(vids)

    # Deduplicate by URL
    seen = set()
    uniq: list[dict] = []
    for v in all_videos:
        url = v.get("webpage_url")
        if not url or url in seen:
            continue
        seen.add(url)
        uniq.append(v)

    print(f"[sora_hit_miner] collected {len(uniq)} unique videos before filtering")

    # Filter to meme-style / unhinged videos only
    meme_videos: list[dict] = []
    for v in uniq:
        if is_meme_candidate(v):
            meme_videos.append(v)

    print(f"[sora_hit_miner] {len(meme_videos)} videos after meme filter")

    raw_path = DATA_DIR / "sora_hits_raw.json"
    raw_path.write_text(json.dumps(meme_videos, indent=2), encoding="utf-8")
    print(f"[sora_hit_miner] wrote filtered raw to {raw_path}")

    summaries: list[dict] = []
    for i, v in enumerate(meme_videos, start=1):
        print(f"[sora_hit_miner] summarizing {i}/{len(meme_videos)}: {v.get('title')!r}")
        s = summarize_hit_with_ollama(v)
        summaries.append(s)

    sum_path = DATA_DIR / "sora_hits_summaries.json"
    sum_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"[sora_hit_miner] wrote summaries to {sum_path}")

    # Extract theme seeds into a simple .txt for prompt_agent_smart
    seeds = []
    for s in summaries:
        theme = s.get("theme_seed")
        if theme:
            seeds.append(theme.strip())

    if seeds:
        themes_out = ROOT / "sora_theme_hits.txt"
        themes_out.write_text("\n".join(seeds), encoding="utf-8")
        print(f"[sora_hit_miner] wrote {len(seeds)} theme seeds to {themes_out}")


if __name__ == "__main__":
    main()

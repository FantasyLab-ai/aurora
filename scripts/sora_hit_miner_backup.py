from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import os
import requests  # already in your venv for the Ollama calls

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def run_yt_search(query: str, limit: int = 20) -> list[dict]:
    """
    Use yt-dlp's 'ytsearch' mode to get public metadata for trending AI / Sora-like videos.
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


def summarize_hit_with_ollama(video: dict) -> dict:
    """
    Send a single video (title, description, stats) to DeepSeek via Ollama and
    get back a structured summary of its 'Sora energy'.
    """
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
    model = os.getenv("OLLAMA_MODEL_TREND", "deepseek-r1:8b")

    title = video.get("title") or ""
    desc = video.get("description") or ""
    views = video.get("view_count") or 0
    uploader = video.get("uploader") or ""
    webpage_url = video.get("webpage_url") or ""

    system_msg = (
        "You are analyzing viral AI video content (often created with Sora or similar). "
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
        # Try to parse JSON; if it fails, just wrap raw text.
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
    # You can tweak these queries anytime
    queries = [
        "sora ai video 10 seconds",
        "sora ai insane prompt",
        "hyper realistic ai video sora",
        "viral ai video cinematic 9:16",
    ]

    all_videos: list[dict] = []
    for q in queries:
        vids = run_yt_search(q, limit=15)
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

    print(f"[sora_hit_miner] collected {len(uniq)} unique videos")

    raw_path = DATA_DIR / "sora_hits_raw.json"
    raw_path.write_text(json.dumps(uniq, indent=2), encoding="utf-8")
    print(f"[sora_hit_miner] wrote raw to {raw_path}")

    summaries: list[dict] = []
    for i, v in enumerate(uniq, start=1):
        print(f"[sora_hit_miner] summarizing {i}/{len(uniq)}: {v.get('title')!r}")
        s = summarize_hit_with_ollama(v)
        summaries.append(s)

    sum_path = DATA_DIR / "sora_hits_summaries.json"
    sum_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"[sora_hit_miner] wrote summaries to {sum_path}")

    # Optionally extract theme seeds you like into a simple .txt for prompt_agent_smart
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

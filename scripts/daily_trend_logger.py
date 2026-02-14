import requests
import json
import os
import datetime
from time import sleep

OLLAMA_SERVER = "http://localhost:11434"
MODEL_NAME = "mistral"  # or other model installed

RAW_TRENDS_FILE = "raw_trends.json"
CLUSTERED_TRENDS_FILE = "clustered_trends.json"
GENERATED_PROMPTS_FILE = "generated_prompts.json"
TREND_LOG_DIR = os.path.expanduser("~/FantasyAI/trend_logs")

os.makedirs(TREND_LOG_DIR, exist_ok=True)


def fetch_trends():
    prompt = (
        "Give me a list of the top 10 *very recent* social media trends involving artificial intelligence, "
        "video content, or viral digital culture from TikTok and YouTube. Be specific and up to date. Return "
        "just the 10 trends as a numbered list, no commentary."
    )

    try:
        print("🚀 Fetching AI trends from Ollama...")
        response = requests.post(
            f"{OLLAMA_SERVER}/api/chat",
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=120
        )
        response.raise_for_status()
        output = response.json()

        # Get the actual LLM message content
        raw_text = output['message']['content']

        # Parse into trend list
        trends = [line.split(". ", 1)[1] for line in raw_text.strip().splitlines() if ". " in line]
        return trends

    except Exception as e:
        raise RuntimeError(f"Failed to fetch trend data: {e}")


def save_json(data, filename):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)


def save_log_file(trends):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(TREND_LOG_DIR, f"trend_log_{timestamp}.txt")
    with open(log_path, "w") as f:
        for i, trend in enumerate(trends, 1):
            f.write(f"{i}. {trend}\n")
    return log_path


def cluster_trends(trends):
    prompt = (
        "Group the following AI-related social media trends into 2–3 labeled clusters by theme. "
        "Respond in raw JSON format like: {\"clusters\": [{\"label\": \"Theme A\", \"trends\": [...]}, ...]}\n\n"
        f"Trends:\n{json.dumps(trends)}"
    )

    try:
        response = requests.post(
            f"{OLLAMA_SERVER}/api/chat",
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=120
        )
        response.raise_for_status()
        output = response.json()
        raw = output['message']['content']
        return json.loads(raw)

    except Exception as e:
        raise RuntimeError(f"Failed to cluster trends: {e}")


def generate_video_prompts(clusters):
    prompts = []
    for cluster in clusters.get("clusters", []):
        label = cluster["label"]
        top_trend = cluster["trends"][0]

        prompt_text = (
            f"Generate a detailed 8-second Google Veo video prompt based on the trend \"{top_trend}\" "
            f"from the category \"{label}\". It should be photorealistic, comedic or musical in style, and "
            f"use vlog-style selfie camera angles. Format the prompt to match Google Veo’s cinematic realism requirements."
        )

        try:
            response = requests.post(
                f"{OLLAMA_SERVER}/api/chat",
                json={
                    "model": MODEL_NAME,
                    "messages": [{"role": "user", "content": prompt_text}]
                },
                timeout=120
            )
            response.raise_for_status()
            result = response.json()
            content = result['message']['content']
            prompts.append({
                "category": label,
                "trend": top_trend,
                "prompt": content.strip()
            })

        except Exception as e:
            print(f"⚠️ Failed to generate prompt for {label} → {top_trend}: {e}")

        sleep(1)  # slight delay between API calls

    return prompts


def main():
    try:
        trends = fetch_trends()

        # Save raw trends to file
        save_json(trends, RAW_TRENDS_FILE)

        # Save readable .txt log
        log_path = save_log_file(trends)
        print(f"\n✅ Trends saved to {log_path}\n")
        print("📈 Top Trends:\n")
        for i, t in enumerate(trends, 1):
            print(f" {i}. {t}")

        # Step 2: Cluster trends
        clusters = cluster_trends(trends)
        save_json(clusters, CLUSTERED_TRENDS_FILE)
        print(f"\n🧠 Trends clustered into labeled groups and saved to {CLUSTERED_TRENDS_FILE}")

        # Step 3: Generate prompts
        prompts = generate_video_prompts(clusters)
        save_json(prompts, GENERATED_PROMPTS_FILE)
        print(f"\n🎬 Prompts generated and saved to {GENERATED_PROMPTS_FILE}")

    except Exception as err:
        print(f"❌ Failed to fetch or save trend data: {err}")


if __name__ == "__main__":
    main()

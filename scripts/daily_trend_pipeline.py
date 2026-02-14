#!/usr/bin/env python3

import os
import json
import requests
from datetime import datetime
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

# === CONFIG ===
OLLAMA_MODEL = "mistral"  # Or use "deepseek-coder:6.7b", etc.
OLLAMA_URL = "http://localhost:11434/api/generate"
BASE_DIR = os.path.expanduser("~/FantasyAI/scripts")
LOG_DIR = os.path.join(BASE_DIR, "trend_logs")
PROMPT_DIR = os.path.join(BASE_DIR, "generated_prompts")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PROMPT_DIR, exist_ok=True)

# === 1. MOCK Trend Fetcher (replace with real trend API later) ===
def fetch_trends():
    print("📊 Fetching trends...")
    trends = [
        "Dancing Videos",
        "TikTok Duets",
        "Instagram Reels",
        "ASMR",
        "Unboxing Videos",
        "Prank Videos",
        "Makeup Tutorials",
        "Fitness Challenges",
        "Pet Content",
        "Cooking Videos"
    ]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"trend_log_{timestamp}.txt")
    with open(log_path, "w") as f:
        for i, trend in enumerate(trends, 1):
            f.write(f"{i}. {trend}\n")
    print(f"📁 Trends logged to: {log_path}")
    return trends

# === 2. Cluster Trends and Select One ===
def cluster_trends(trends, n_clusters=3):
    print("🧠 Clustering trends...")
    vectorizer = TfidfVectorizer(stop_words='english')
    X = vectorizer.fit_transform(trends)
    kmeans = KMeans(n_clusters=n_clusters, random_state=0, n_init=10)
    labels = kmeans.fit_predict(X)

    # Choose most frequent label
    label_counts = {i: list(labels).count(i) for i in range(n_clusters)}
    top_label = max(label_counts, key=label_counts.get)
    cluster_trends = [trends[i] for i in range(len(trends)) if labels[i] == top_label]
    chosen = cluster_trends[0] if cluster_trends else trends[0]
    print(f"🔥 Selected Trend for Prompt: {chosen}")
    return chosen

# === 3. Generate Prompt with Ollama ===
def generate_prompt(trend):
    print("🎬 Generating prompt with Ollama...")
    system_prompt = f"""
Write an ultra-realistic, 8-second Google Veo 3 video prompt inspired by this trend: {trend}.
The video should include upright walking, humanlike animal vloggers with cinematic, natural scenery, grounded humor, and subtle emotion.
Describe the full scene with detailed environment, camera POV (like selfie stick or handheld), character description, and natural-sounding dialogue.
"""

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": system_prompt,
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        return response.json()["response"].strip()
    except requests.RequestException as e:
        raise RuntimeError(f"Ollama error: {e}")

# === 4. Save Prompt to File ===
def save_prompt(prompt, trend):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_trend = trend.replace(" ", "_").lower()
    filename = f"veo_prompt_{safe_trend}_{timestamp}.txt"
    full_path = os.path.join(PROMPT_DIR, filename)
    with open(full_path, "w") as f:
        f.write(prompt)
    print(f"💾 Prompt saved to: {full_path}")

# === MAIN RUN ===
def main():
    trends = fetch_trends()
    selected_trend = cluster_trends(trends)
    prompt = generate_prompt(selected_trend)
    save_prompt(prompt, selected_trend)

if __name__ == "__main__":
    main()

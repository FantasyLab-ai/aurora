import requests

OLLAMA_SERVER = "http://10.0.0.237:11434"  # <-- Your Windows IP here

payload = {
    "model": "llama3",
    "prompt": "What are 3 trending TikTok video topics this week?",
    "stream": False
}

try:
    response = requests.post(f"{OLLAMA_SERVER}/api/generate", json=payload)
    data = response.json()
    print("🔍 Trending TikTok Topics:")
    print(data["response"])
except Exception as e:
    print("❌ Failed to fetch from Ollama:", e)

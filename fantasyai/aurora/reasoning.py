# fantasyai/aurora/reasoning.py
import requests
import json

def ask_deepseek(prompt, model="deepseek-r1:8b"):
    payload = {"model": model, "prompt": prompt, "stream": False}
    r = requests.post("http://localhost:11434/api/generate", json=payload)
    r.raise_for_status()
    return r.json()["response"]

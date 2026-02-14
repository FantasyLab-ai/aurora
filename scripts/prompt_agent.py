from __future__ import annotations
import importlib, os, random

def get_topics(n: int = 5):
    # Try your existing social agent if present
    try:
        ssa = importlib.import_module("scripts.social_source_agent")
        tops = ssa.get_social_trends_top_n(n)  # adjust to your function name if different
        if tops: return tops[:n]
    except Exception:
        pass
    # Fallback seed topics
    return [
        "wildlife conservationist placing baby owls back in their burrow",
        "urban wildlife rescue story",
        "rare cat sighting in India",
        "wildlife trafficking penalties debate",
        "perfect animal photo moment"
    ][:n]

BASE_TEMPLATES = [
    "Create a cinematic 10–12s short about: {topic}. Emphasize emotion, quick cuts, and close-ups.",
    "Make a 9–11s vertical video on: {topic}. Use natural light, two camera angles, and ambient audio.",
    "Generate a 12s montage for: {topic}. Start with a hook in 1s, then 3 action beats, end on a branded sting."
]

def candidate_prompts(topic: str, k: int = 3):
    xs = []
    for t in random.sample(BASE_TEMPLATES, len(BASE_TEMPLATES)):
        xs.append(t.format(topic=topic))
    return xs[:k]

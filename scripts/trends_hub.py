# scripts/trends_hub.py
from typing import List, Optional

FALLBACK = [
    "stair sprint challenge",
    "gas station chaos",
    "overnight in a laundromat",
    "mall parking lot drift",
    "storm drain exploration",
    "DIY fireworks gone wrong",
]

def get_trends_for_niche(niche: str, hint: Optional[str] = None) -> List[str]:
    """
    Return ranked trend topics for this niche.
    Later: connect to TikTok metrics / your anomaly signals.
    """
    ideas = FALLBACK.copy()
    if hint and hint not in ideas:
        ideas.insert(0, hint)
    return ideas

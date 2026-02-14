# scripts/caption_hashtag.py
def make_caption_bundle(topic: str, niche: str):
    """
    Returns auto-caption, CTA, and hashtags.
    You’ll replace with smarter prompt logic / LLM call.
    """
    base_caption = f"{topic} // {niche} // proof of grind."
    hashtags = [
        "#ai",
        "#viral",
        "#fyp",
        "#motivation",
        "#fantasylab",
    ]
    cta = "Follow @fantasylab.ai for daily drops."

    return {
        "caption": base_caption,
        "hashtags": " ".join(hashtags),
        "cta": cta,
    }

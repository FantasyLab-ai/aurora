import textwrap

GENERIC_HASHTAGS = "#fyp #ai #fantasylabai #sora #fantasylab"


def _shorten_topic(topic: str, max_len: int = 80) -> str:
    t = (topic or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def generate_caption_and_hashtags(topic: str):
    """
    Deterministic, low-chaos caption generator.
    Always returns:
        caption: 1–2 short lines referencing the trend text
        hashtags: fixed FantasyLab hashtag pack
    """
    short_topic = _shorten_topic(topic)

    if not short_topic:
        caption = "This video should not look this real."
    else:
        # Simple, flexible pattern that works for any trend
        caption = f"{short_topic}"

    return caption, GENERIC_HASHTAGS

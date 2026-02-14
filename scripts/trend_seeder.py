import sqlite3
import time
import random
import textwrap
import pathlib
import sys

import re
import requests
from caption_forge import generate_caption_and_hashtags

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "jobs.db"
SORA_MINED_PATH = ROOT / "sora_explore_themes.txt"

def load_sora_mined_trends(max_items: int = 100) -> list[str]:
    if not SORA_MINED_PATH.exists():
        log(f"no mined Sora theme file at {SORA_MINED_PATH}")
        return []

    lines = SORA_MINED_PATH.read_text(encoding="utf-8").splitlines()
    clean = []
    for L in lines:
        t = L.strip()
        if not t:
            continue
        clean.append(t)
        if len(clean) >= max_items:
            break

    log(f"loaded {len(clean)} Sora-mined themes.")
    return clean


def log(msg: str) -> None:
    print(f"[trend_seeder] {msg}", flush=True)


def ensure_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    # Make sure jobs table exists with at least the core columns.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,
            platform TEXT,
            caption TEXT,
            hashtags TEXT,
            cta TEXT,
            file_path TEXT,
            prompt TEXT,
            status TEXT,
            created_at REAL
        )
        """
    )
    db.commit()
    # Log columns for sanity
    cols = [row[1] for row in cur.execute("PRAGMA table_info(jobs)")]
    log(f"jobs columns: {', '.join(cols)}")
    return db


def fetch_raw_trends(limit: int = 50) -> list[str]:
    """
    Fetch a bunch of candidate trend titles from Reddit.
    We keep it simple: r/all hot.
    """
    log("fetching raw Reddit trends...")
    url = "https://www.reddit.com/r/all/hot.json"
    headers = {
        "User-Agent": "FantasyAI-trend-seeder/1.0 (by u/fantasylab)"
    }
    params = {"limit": str(limit)}
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    out: list[str] = []
    for child in data.get("data", {}).get("children", []):
        title = (
            child.get("data", {})
            .get("title", "")
            .strip()
        )
        if title:
            out.append(title)
    log(f"got {len(out)} raw Reddit trends.")
    return out


_POLITICAL_KEYWORDS = [
    "trump",
    "biden",
    "maga",
    "democrat",
    "republican",
    "election",
    "senate",
    "congress",
    "parliament",
    "prime minister",
    "president",
    "gaza",
    "israel",
    "palestine",
    "ukraine",
    "putin",
    "xi jinping",
    "rnc",
    "dnc",
    "supreme court",
]


def looks_political(title: str) -> bool:
    t = title.lower()
    return any(word in t for word in _POLITICAL_KEYWORDS)


def filter_trends(raw: list[str]) -> list[str]:
    clean = []
    for t in raw:
        if looks_political(t):
            continue
        # Very basic: avoid obvious NSFW / super dark stuff too
        if any(bad in t.lower() for bad in ["nsfw", "murder", "suicide", "beheading"]):
            continue
        clean.append(t.strip())
    log(f"{len(clean)} trends after filtering politics/NSFW.")
    return clean


# === Prompt templates (meme / brainrot but physically accurate) ===


def _dedent(s: str) -> str:
    return textwrap.dedent(s).strip() + "\n"


def prompt_news_chopper_bear_lawnmower(topic: str) -> tuple[str, str]:
    style_label = "local news chopper bear on a lawnmower"
    caption = f"{topic} but filmed like a low-speed bear lawnmower chase on live news"
    prompt = _dedent(
        f"""
        10-second clip.
        Ultra-realistic 4K local news helicopter footage, 9:16 vertical crop from 16:9, 30fps.
        Stabilized camera looking down at a multi-lane freeway in bright daylight. Traffic is light and moving normally in all lanes.

        Reddit trend inspiration (do NOT overlay this as text, just vibe off it):
        "{topic}"

        In the middle lane, we see a photorealistic brown bear sitting upright, calmly driving a bright green riding lawnmower at a very slow speed straight down the freeway. The lawnmower wheels track perfectly in the lane; the blades are off. Cars behind it crawl along patiently with their hazard lights on, no crashes, no crazy swerving-just a surreal slow procession. Every vehicle’s motion and suspension is realistic and respects weight and friction.

        Two police cruisers follow behind the lawnmower with lights on, but at the same hilariously slow speed. It feels like a completely normal traffic situation, except for the bear.

        Camera angle stays like real breaking news coverage from the chopper. A simple lower-third graphic reads “LIVE - FREEWAY INCIDENT”.

        Audio: only the news anchor and chopper reporter talking over the footage in real, slightly compressed broadcast voices (NOT robotic).
        They react with disbelief but keep it play-by-play, describing it like a “low-speed pursuit where a bear is somehow following all traffic laws.”
        No one on the ground speaks; the bear never talks.

        All visuals must be photorealistic: correct shadows, scale, and vehicle physics.
        No cartoon look, no floating, no clipping between bear, lawnmower, and road.

        Negative prompts:
        cartoon, anime, Pixar, CGI render, stylized, low-res, blurry, jittery motion, floating, clipping, distorted anatomy, meme text,
        subtitles, TikTok UI, emojis, watermarks, logos, glitch/VHS effects, slow motion, time-lapse, oversaturated neon colors,
        robotic or text-to-speech voices.
        """
    )
    return caption, prompt


def prompt_ring_cat_delivery(topic: str) -> tuple[str, str]:
    style_label = "Ring doorbell cat delivery driver"
    caption = f"{topic} but it’s a cat Amazon driver on the Ring cam again"
    prompt = _dedent(
        f"""
        10-second clip.
        Ultra-realistic 4K Ring-style doorbell video, 9:16 vertical, 30fps.
        Fixed shot of a typical suburban front porch in mid-day: small steps, welcome mat, one potted plant, street and parked cars visible in the background.

        Reddit trend inspiration (do NOT overlay this as text, just vibe off it):
        "{topic}"

        From the right side of frame, a photorealistic orange tabby cat in a slightly oversized delivery uniform
        (blue jacket, baseball cap, handheld scanner on a belt clip) appears, struggling to carry a huge, comically large cardboard box
        that’s almost bigger than the cat. The box is realistically heavy: the cat leans back, legs straining,
        the box corners bump the steps and doorframe exactly how a real package would.

        The cat slowly, carefully shuffles up the steps and drops the box on the doormat with a deep, believable thud that slightly shakes
        the camera view like a real doorbell clip. It steps back, pulls out the small scanner, and snaps a photo of the box.

        Ambient audio: tiny click-beep of the scanner, muffled neighborhood sounds, a distant car passing.
        From just inside the house, picked up by the doorbell mic, we hear a human voice shout in genuine surprise:
        “WHY IS THE DELIVERY GUY A CAT AGAIN? WHAT DID I ORDER THIS TIME?”
        That’s it-no other dialogue. The cat never speaks.

        Everything is photorealistic: cardboard texture, shadows, cat fur, lens distortion typical of doorbell cams.
        No cartoon look, no floating or clipping between paws, box, and steps.

        Negative prompts:
        cartoon, anime, stylized, Pixar, CGI-render look, low-res, blurry, jittery motion, floating, clipping, distorted anatomy,
        meme text, subtitles, TikTok UI, emojis, watermarks, logos, VHS/glitch filters, slow motion, time-lapse,
        oversaturated neon colors, robotic or text-to-speech voices.
        """
    )
    return caption, prompt


def prompt_bodycam_yeti_shoveling(topic: str) -> tuple[str, str]:
    style_label = "cop bodycam yeti shoveling driveways"
    caption = f"{topic} but a police bodycam catches a yeti shoveling everyone’s driveway"
    prompt = _dedent(
        f"""
        10-second clip.
        Ultra-realistic 4K police bodycam footage, 9:16 vertical crop from 16:9, 60fps.
        Slight real-world fisheye, timestamp in the corner. The camera shakes naturally as an officer walks up a quiet suburban street
        at dawn after a big snowstorm. Parked cars and driveways are buried in fresh snow, breath fogs in the cold air.

        Reddit trend inspiration (do NOT overlay this as text, just vibe off it):
        "{topic}"

        At the end of a driveway, a photorealistic white yeti in work boots and a neon reflective vest is shoveling snow at insane speed
        but still with believable physics: every scoop throws a heavy arc of snow that lands realistically; piles build up correctly along
        the edges. The yeti’s fur is matted with frost, muscles moving under the fur as it works.

        The officer stops on the sidewalk, watching as the yeti finishes one driveway, then immediately jogs to the neighbor’s
        and starts shoveling there too, still physically grounded, no sliding.

        Audio is only from the bodycam mic: boot crunches in the snow, shovel scraping, the officer quietly radioing in a natural,
        slightly out-of-breath voice (NOT robotic), saying something like they’re “responding to a disturbance that’s actually just
        free snow removal by a seven-foot-tall whatever-this-is.” No one on screen speaks directly to camera; the yeti never talks.

        Everything is photorealistic: correct snow texture and spray, realistic shovel motion, natural lighting and shadows.
        No cartoon look, no floating or clipping between shovel, snow, and ground.

        Negative prompts:
        cartoon, anime, Pixar, CGI render, stylized, low-res, blurry, jittery motion, floating, clipping, distorted anatomy,
        meme text, subtitles, TikTok UI, emojis, watermarks, logos, glitch/VHS effects, slow motion, time-lapse,
        oversaturated neon colors, robotic or text-to-speech voices.
        """
    )
    return caption, prompt


def prompt_news_giant_cat(topic: str) -> tuple[str, str]:
    style_label = "news chopper giant cat traffic jam"
    caption = f"{topic} but the traffic report is a giant cat sleeping on the highway"
    prompt = _dedent(
        f"""
        10-second clip.
        Ultra-realistic 4K local news helicopter footage, 9:16 vertical crop from 16:9, 30fps.
        Stabilized camera flying over a major multi-lane highway at midday. Sun is high, shadows short,
        traffic backed up for miles in both directions.

        Reddit trend inspiration (do NOT overlay this as text, just vibe off it):
        "{topic}"

        In the middle of the shot, across several lanes of stopped cars, lies a photorealistic giant gray house cat,
        easily the size of a small office building, curled up and sleeping right across the highway like it’s a warm rug.
        Its fur detail is high-res; chest rises and falls with slow, heavy breaths. Cars are stopped neatly just short of its body,
        hazard lights blinking, no crashes or chaos.

        Occasional tiny cars in the far lane slowly U-turn on the shoulder to escape, but most are pinned in.
        A couple of tiny people stand outside their cars filming with phones, all motion physically grounded, no jitter.

        A simple lower-third graphic says “LIVE - TRAFFIC ALERT”.

        Audio: only the anchor and chopper reporter in realistic news voices (slightly compressed, NOT robotic).
        They react to what we see, riffing about “an absolutely massive house cat shutting down three counties of morning commute”
        and “at least it’s not scratching the asphalt.” No on-ground voices are heard; the cat never moves
        except for subtle breathing and maybe a small ear twitch.

        All visuals must be photorealistic: correct scale, shadows, and reflections on car roofs.
        No cartoon style, no floating or clipping where the cat touches the road.

        Negative prompts:
        cartoon, anime, Pixar, CGI render, stylized, low-res, blurry, jittery motion, floating, clipping, distorted scale,
        meme text, subtitles, TikTok UI, emojis, watermarks, logos, glitch/VHS effects, slow motion, time-lapse,
        oversaturated neon colors, robotic or text-to-speech voices.
        """
    )
    return caption, prompt


PROMPT_TEMPLATES = [
    prompt_news_chopper_bear_lawnmower,
    prompt_ring_cat_delivery,
    prompt_bodycam_yeti_shoveling,
    prompt_news_giant_cat,
]


def build_prompt_for_topic(topic: str) -> tuple[str, str]:
    """
    Deterministically pick a template per topic so reruns are stable.
    Returns (caption, prompt).
    """
    idx = abs(hash(topic)) % len(PROMPT_TEMPLATES)
    fn = PROMPT_TEMPLATES[idx]
    caption, prompt = fn(topic)
    return caption, prompt


def get_existing_topics(db: sqlite3.Connection) -> set[str]:
    cur = db.cursor()
    rows = cur.execute("SELECT topic FROM jobs").fetchall()
    existing = {r[0] for r in rows if r[0]}
    log(f"existing topics in DB: {len(existing)}")
    return existing


def insert_jobs(db: sqlite3.Connection, topics: list[str]) -> None:
    cur = db.cursor()
    existing = get_existing_topics(db)
    new_count = 0
    for topic in topics:
        if topic in existing:
            log(f"skipping existing topic already in DB: '{topic}'")
            continue

        # Build the Sora prompt text using our templates
        _, prompt = build_prompt_for_topic(topic)

        # Build caption + hashtags via caption_forge
        cap, tags = generate_caption_and_hashtags(topic)

        cta = "follow @fantasylab.ai for more unhinged AI videos"
        platform = "tiktok"
        status = "todo"
        created_at = time.time()
        log(f"inserting NEW topic: '{topic}' with caption: {cap!r}")
        cur.execute(
            '''
            INSERT INTO jobs (topic, platform, caption, hashtags, cta, file_path, prompt, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (topic, platform, cap, tags, cta, "", prompt, status, created_at),
        )
        new_count += 1
    db.commit()
    log(f"inserted {new_count} NEW jobs into jobs.db")



def main() -> None:
    log(f"DB path: {DB_PATH}")
    db = ensure_db()

    raw = fetch_raw_trends(limit=50)
    clean = filter_trends(raw)

    if not clean:
        log("no trends after filtering; nothing to insert.")
        return

    # Pick up to 5 distinct topics per run
    random.shuffle(clean)
    chosen = clean[:5]
    insert_jobs(db, chosen)


if __name__ == "__main__":
    main()





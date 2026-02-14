# scripts/prompt_builder.py
from __future__ import annotations
import random, re
from typing import Dict, Any, List, Optional

# --- Helpers -----------------------------------------------------------------

def _kw(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip())

def _pick(items: List[str]) -> str:
    return random.choice(items)

def _maybe_explicit(line: str, allow_explicit: bool) -> str:
    if allow_explicit:
        return line
    # tone down profanity while keeping punch
    return (line
            .replace("fucking", "freaking")
            .replace("f*ing", "freaking")
            .replace("shit", "stuff")
            .replace("ass", "butt"))

def _weight_by_keywords(trend: str, mapping: Dict[str, List[str]], default: str) -> str:
    t = trend.lower()
    best = default
    score = -1
    for scene, keys in mapping.items():
        s = sum(1 for k in keys if k in t)
        if s > score:
            score = s
            best = scene
    return best

# --- Scene Library ------------------------------------------------------------
# Each archetype defines: environment, camera, characters, artifacts, vibe,
# plus "dialogs" (pairs of lines) and a "hooks" list for visual hook emphasis.

SCENES: Dict[str, Dict[str, Any]] = {
    "stair_sprint": {
        "keywords": ["stair", "stairs", "sprint", "challenge", "8s"],
        "environment": _kw("""
            A public stairwell outdoors at dawn. Metal handrails beaded with dew.
            Concrete steps dark with moisture; faint traffic hiss in the distance.
            Pigeons flutter up when the camera lifts; cool air, visible breath.
        """),
        "camera": _kw("""
            Handheld vertical vlog; chest-level framing; authentic stride bounce;
            brief motion blur on whip-pans; rolling-shutter wobble on fast swings.
        """),
        "characters": _kw("""
            • Yeti (subject): 7' tall, gray-white fur damp with dew, wearing a torn windbreaker and
              a hydration pack; determined eyes, exhausted breaths.
            • Bear (cameraperson): 6'6", brown fur, backwards cap, snorts laughter between breaths.
        """),
        "dialogs": [
            ('Yeti (gasping): "Eight seconds. No bullshit—hit the climb!"',
             'Bear (off, wheezing): "Move it! You got one rep!"'),
            ('Yeti (leaning into lens): "Timer’s on. Prove you’re alive."',
             'Bear (off, cackling): "Knees up or log off!"'),
        ],
        "artifacts": _kw("""
            Real handheld jitter; slight mic peak on loud words; breath pops on mic;
            lens catches dew droplets; footfalls thud; distant traffic wash.
        """),
        "vibe": _kw("Hyper-real sprint micro-challenge; gritty; motivational absurdity; no cartoon traits."),
        "hooks": ["phone timer flashes 00:08 → 00:00", "condensed breath in cold air", "hard footfall echo"],
    },

    "gas_station_meltdown": {
        # closest to your Example #1
        "keywords": ["gas", "station", "pump", "highway", "geysers", "daylight"],
        "environment": _kw("""
            A small rural gas station off a two-lane highway, 3:15 PM. Concrete forecourt cracked,
            oil-stained. One fluorescent tube hums above a dusty vending machine. Pump #3 has a torn
            “OUT OF ORDER” bag flapping. A pickup idles far off. The hook: a snapped hose sprays a
            violent arc of water like a geyser, rainbow glare in sun; cicadas buzz, wind in cornfields.
        """),
        "camera": _kw("""
            Handheld vlog POV—Yeti grips a selfie stick; camera bobs with chest movement.
            Water droplets streak the lens until clumsily wiped; autofocus hunts between spray and Bear.
        """),
        "characters": _kw("""
            • Yeti (camera op): 7’ tall, pale-gray fur damp from mist, wearing a neon station polo two sizes too small.
            • Bear (subject): 6’6”, brown fur soaked, backwards trucker hat; tank top plastered to chest, deadpan sarcasm.
        """),
        "dialogs": [
            (_maybe_explicit('Yeti (yelling): "This pump just nutted into orbit—look at this shit!"', True),
             _maybe_explicit('Bear (soaked, furious): "I’m about to sue Chevron and Jesus at the same time!"', True)),
            (_maybe_explicit('Yeti (wheezing): "Bro it’s a rainbow geyser—nature’s lawsuit."', True),
             _maybe_explicit('Bear (slips): "I did NOT sign the splash zone waiver!"', True)),
        ],
        "artifacts": _kw("""
            Water hiss and pump hum; droplets smear the lens; autofocus locks on rainbow then snaps back; mic distorts on shouts.
        """),
        "vibe": _kw("Hyper-realistic absurdity; raw gas-station vlog gone wrong; blunt but grounded humor."),
        "hooks": ["geyser arc rainbow", "slip-in-puddle moment", "OUT OF ORDER bag flapping"],
    },

    "subway_tribunal": {
        # your Example #2
        "keywords": ["subway", "rats", "platform", "bench", "pizza", "nyc"],
        "environment": _kw("""
            NYC subway platform at midday. Flickering fluorescent buzz. Stained tiles. A greasy puddle
            creeps near the edge. Trash skitters; a half-eaten pizza slice slides slowly. A battered
            steel bench sits under a harsh lamp, cinematic and unforgiving.
        """),
        "camera": _kw("""
            Handheld vlog; Bear’s huge paw around foam grip; cam sways with breath; occasional tilt shows wet fur clumps.
            Autofocus wrestles between Yeti gesturing and three massive subway rats perched like a jury.
        """),
        "characters": _kw("""
            • Bear (camera op): 6’6”, soaked fur at ankles, bright yellow “I ❤️ NY” hoodie stretched tight.
            • Yeti (subject): 7’, fur matted, cargo shorts, no shirt, padlock chain; pacing, stressed.
            • Subway Rats (3): dog-sized, slick whiskers; one chews a cigarette like a cigar; eerily calm.
        """),
        "dialogs": [
            (_maybe_explicit('Bear (whisper): "Bro… it’s a freaking tribunal. They’re about to sentence him."', False),
             _maybe_explicit('Yeti (to rats, panicked): "I didn’t take the pizza! Quit lookin’ at me like that!"', False)),
            (_maybe_explicit('Bear (off, nervous laugh): "He’s cooked. They found him guilty."', False),
             'Rat (drops cigarette, squeaks loudly): *judgment rendered*'),
        ],
        "artifacts": _kw("""
            Lens flare from overhead lamp; rat tail slaps lens leaving greasy smear; mic distorts as train blasts;
            garbled announcement; hasty wipe causes brief blur.
        """),
        "vibe": _kw("Hyper-real absurdism; grimy texture; candid NYC energy; raw panic + comedy."),
        "hooks": ["rats in a perfect row", "cigarette-chewing rat", "pizza slice sliding on grease"],
    },

    "woods_on_shrooms": {
        "keywords": ["forest", "woods", "mushroom", "shrooms", "psychedelic", "hike"],
        "environment": _kw("""
            Foggy pine forest near dusk. Damp earth; bioluminescent-looking mushrooms glowing faintly at the base of a log.
            Distant owl; wind combs treetops; particulate mist catches stray sunbeams.
        """),
        "camera": _kw("""
            Handheld vlog; slow, deliberate sways; occasional rack-focus from Yeti eyes to a cluster of mushrooms;
            subtle chromatic aberration-like light leaks for realism (not cartoon).
        """),
        "characters": _kw("""
            • Yeti (subject): wide pupils, fur slightly matted with condensation; whispering like trees can hear.
            • Bear (off-cam): gently hyping him, trying not to laugh.
        """),
        "dialogs": [
            (_maybe_explicit('Yeti (hushed): "Every pine needle is a cathedral. Be respectful."', False),
             _maybe_explicit('Bear (off, whisper-laugh): "Touch grass, prophet."', False)),
            (_maybe_explicit('Yeti (to lens): "If you’re still doomscrolling—go outside. Let the wind roast you."', True),
             _maybe_explicit('Bear: "That’s… not how wind works."', False)),
        ],
        "artifacts": _kw("""
            Real wind rumble; mic grazes fur causing soft bass thumps; breath fogs lower frame; fingertip smears lens briefly.
        """),
        "vibe": _kw("Hyper-real nature trance; intimate; grounded sensory humor; zero cartoon physics."),
        "hooks": ["mushroom cluster shimmer", "pupil dilation reflection", "breath fog creeping in frame"],
    },
}

# --- Public API ---------------------------------------------------------------

def build_niche_prompt(
    niche: str,
    trend: str,
    scene: str = "auto",
    allow_explicit: bool = True,
    duration_s: int = 8,
    orientation: str = "9:16",
) -> str:
    """
    Returns a Veo-ready hyper-real prompt text built from:
    - niche (brand voice)
    - current trend (steers topic + scene)
    - scene archetype (auto-picked by trend keywords or explicit override)
    """
    trend = _kw(trend)
    niche = _kw(niche)

    # choose scene
    if scene == "auto":
        # map scene->keywords for selection
        mapping = {k: v["keywords"] for k, v in SCENES.items()}
        # default preference depends on trend text
        default_scene = "stair_sprint"
        scene_key = _weight_by_keywords(trend, mapping, default_scene)
    else:
        scene_key = scene if scene in SCENES else "stair_sprint"

    s = SCENES[scene_key]

    # pick a dialogue pair (keeps it ≤8s feel)
    line_a, line_b = _pick(s["dialogs"])
    line_a = _maybe_explicit(line_a, allow_explicit)
    line_b = _maybe_explicit(line_b, allow_explicit)

    # mix in niche + trend to strengthen grounding / SEO hooks without breaking realism
    hook_note = f" Tie directly to today’s trend: {trend}."
    niche_note = f" Style stays true to niche: {niche}."

    prompt = f"""Environment • {_kw(s['environment'])}
Camera • {_kw(s['camera'])}
Characters • {_kw(s['characters'])}
Action & Dialogue (≤ {duration_s} s) • {line_a} • {line_b}
Artifacts • {_kw(s['artifacts'])}
Vibe • {_kw(s['vibe'])}
Duration • {duration_s} seconds exact.
Orientation • {orientation}.
Notes •{hook_note}{niche_note}
Quality • Hyper-realistic, human motion/physics, real audio texture, zero cartoon traits.
"""
    return prompt

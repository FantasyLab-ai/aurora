import os, time, random

# Map trending theme -> tone for scene/voice
_TONE = {
 "Cute Pet Compilation":"comedy", "Funny Animal Moments":"comedy", "Pet Reactions":"comedy",
 "Nature Beauty":"awe", "Nature Sounds":"awe",
 "Wildlife Facts":"docu", "Wildlife Conservation":"docu",
 "Fantasy Creatures":"docu", "Outdoor Adventure":"awe",
 "Tech News":"docu", "Gaming Moments":"comedy"
}

_SCENES = {
 "comedy": [
   ("Laundromat between cycles, 6:54 PM. chrome reflections; coin clinks.",
    "Handheld vlog, quick push-ins; intentional micro-jitters."),
   ("Kitchen counter at midnight; buzzing fridge; stray kibble.",
    "Phone vertical; snap zooms; jump-cuts.")
 ],
 "awe": [
   ("Alpine meadow at golden hour; wind combing long grass.",
    "Tripod; slow pan; ND haze; shallow DOF inserts."),
   ("Foggy forest trail at dawn; dew on needles.",
    "Gimbal, slow drift; wide establishing + macro cutaways.")
 ],
 "docu": [
   ("Ranger hut; maps pinned; shortwave static; thermos ring on wood.",
    "Shoulder cam; rack-focus between hands and map grid."),
   ("Creek ravine; trail cam blinking; wet stone.",
    "Drone orbit; overhead insert; gentle tilt reveals.")
 ]
}

def _tone_for(trend: str) -> str:
    return _TONE.get((trend or "").strip(), "comedy")

def _lab(name: str) -> str:
    n = name or "Character"
    if "Yeti" in n:  return "🧍‍♂️ Yeti"
    if "Bear" in n:  return "🐻 Bear"
    if "Kitty" in n or "Cat" in n: return "🐈 Kitty"
    return f"🎭 {n}"

def _segment_title(tone: str) -> str:
    return {"comedy":"The Reaction", "awe":"The Revelation", "docu":"The Investigation"}.get(tone, "The Beat")

def _dialogue(team):
    a = team[0] if team else "Yeti A"
    b = team[1] if len(team) > 1 else "Bear A"
    la, lb = _lab(a), _lab(b)
    # 2–3 short lines, clean & safe
    return "\n\n".join([
        f"{la}: Keep rolling. Natural light is perfect.",
        f"{lb}: You seeing this? This looks unreal.",
        f"{la}: Mark it—we’ll cut on that motion."
    ])

def _characters_block(team):
    # Matches pipeline style: blank lines between names
    return "\n\n" + "\n\n".join(team) + "\n"

def _one_prompt(trend: str, team, tone: str, env: str, cam: str, seg_idx: int):
    seg = _segment_title(tone)
    text = (
        f"🎬 {trend}\n"
        f"📋 Segment {seg_idx} - {seg}\n\n"
        f"Environment\n{env}\n\n"
        f"Camera\n{cam}\n\n"
        f"Characters\n{_characters_block(team)}\n"
        f"Dialogue (8s)\n{_dialogue(team)}\n\n"
        f"Artifacts\nSubtle camera shake during movement\nRandom lens specks from environment\n"
        f"Operator's breathing audible in quiet moments\n\n"
        f"Vibe\nFantasyLab.ai raw footage. Trend: {trend}. Tone: {tone} - keep it authentic, never staged."
    )
    meta = {"trend": trend, "team": team, "tone": tone, "seg": seg_idx}
    return ("ok", text, meta)

def generate_candidates(trend: str, teams, n_per_team: int = 2):
    """Return [(critic, text, meta), ...] without any LLM calls."""
    rnd = random.Random(int(time.time()))
    tone = _tone_for(trend)
    scenes = _SCENES.get(tone, _SCENES["comedy"])
    out = []
    for team in teams or []:
        for i in range(n_per_team):
            env, cam = scenes[i % len(scenes)]
            out.append(_one_prompt(trend, team, tone, env, cam, i+1))
    # Stable shuffle to reduce repetition across runs
    rnd.shuffle(out)
    return out

# Keep these names present so import sites don't break
__all__ = ["generate_candidates"]

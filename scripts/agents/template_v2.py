import random
from scripts.memory import recall, remember

SAFE_GUARD_TERMS = ("school shooting","mass shooting","terror","suicide","assault","fatal","dead","bomb")
def _safe_tone(topic):
    t=topic.lower()
    if any(k in t for k in SAFE_GUARD_TERMS):
        return "docu"  # never comedy on tragic topics
    if any(k in t for k in ("cute","funny","meme","prank")): return "comedy"
    if any(k in t for k in ("nature","wildlife","scenery","aurora","storm")): return "awe"
    if any(k in t for k in ("leak","drama","news","launch","update","exposed","rumor")): return "docu"
    return random.choice(["docu","awe","light"])

def _titlelets(topic):
    return random.choice(["The Take","The Discovery","The Reaction","The Quick Pull","The Field Note"])

def _environment_blocks(tone):
    bank={
        "comedy":[("dim bodega aisle","evening","flicker fluorescents","buzz, fridge hum, street muffled")],
        "docu":[("service hallway","afternoon","flat overhead","HVAC hiss, foot echo")],
        "awe":[("alpine ridge","golden hour","warm side-light","wind hiss, cloth flap")],
        "light":[("backyard patio","sunset","string lights bokeh","ice clink, neighbor dog bark")],
    }
    return random.choice(bank.get(tone, bank["docu"]))

def render_topic_prompt(topic:str, team:list[str]):
    tone = _safe_tone(topic)
    loc, tod, light, amb = _environment_blocks(tone)
    mem = recall(topic)  # past props/hooks/etc.
    carry = []
    if mem.get("prop"): carry.append(mem["prop"])
    props = carry or [random.choice(["lost sock","service poster","ripped topo map","traffic cone"])]
    remember(topic,"prop", props[0])

    # dialogue bank per tone (kept clean/brand-safe by default)
    bank={
        "comedy":[
            ("Yeti","Okay but who labeled this '{topic}'?"),
            ("Kitty","Focus—if it's trending, we hit record first, think later."),
            ("Bear","Snack inventory at 80%. Morale holding."),
        ],
        "docu":[
            ("Yeti","Field note: logging '{topic}'. Keep roll sound clean."),
            ("Kitty","Levels steady. Marking ambient now."),
            ("Bear","Copy. Eyes up, look for tells."),
        ],
        "awe":[
            ("Yeti","That light roll might be lens or the air itself."),
            ("Kitty","Everything's louder when we stand still."),
            ("Bear","Hold the frame. Let it breathe."),
        ],
        "light":[
            ("Yeti","Quick sweep, soft feet."),
            ("Kitty","Mic is happy. Don't jinx it."),
            ("Bear","If we find snacks, we log them. Scientifically."),
        ],
    }[tone]

    seg_title=_titlelets(topic)
    char_block="\n\n".join([f"🧍‍♂️ Yeti (camera wrangler / deadpan)", f"🐈 Kitty (operations / razor sarcasm)", f"🐻 Bear (logistics / hungry)"])
    lines=[]
    last=None
    for _ in range(3):
        spk, txt = random.choice(bank)
        if spk==last: spk, txt = random.choice(bank)
        last=spk
        icon = "🧍‍♂️" if spk=="Yeti" else "🐈" if spk=="Kitty" else "🐻"
        lines.append(f"{icon} {spk}: {txt.format(topic=topic)}")

    camera= random.choice([
        "Handheld establishing wobble, then snap to locked tripod.",
        "Floaty gimbal walk-through with controlled pans.",
        "Chest-cam: footfalls visible; roll on emphasis.",
        "Drone follow; gentle altitude changes; orbit on punchlines."
    ])
    artifacts = random.sample([
        "autofocus hunts crossing highlight",
        "brief lens smear clean with thumb",
        "audio clips on close laughter",
        "micro-jitter as grip adjusts"
    ], 2)

    final = "\n".join([
        f"🎬 {topic}",
        f"📋 Segment 1 - {seg_title}",
        "",
        "📰 Topic",
        topic,
        "",
        "📍 Environment",
        f"{loc} at {tod}. Lighting: {light}. Foreground props: {props[0]}. Ambient sound: {amb}.",
        "",
        "🎥 Camera",
        f"{camera} Artifacts: {artifacts[0]}; {artifacts[1]}.",
        "",
        "🧍 Characters",
        "",
        char_block,
        "",
        f"🎤 Dialogue ({random.randint(8,14)}s)",
        "\n\n".join(lines),
        "",
        "🎭 Vibe",
        f"FantasyLab.ai hyper-real vlog with Yeti, Kitty, Bear. Topic: {topic}. Tone: {tone}. Stay grounded; avoid speculation.",
    ])

    prompt_json={
        "topic": topic,
        "tone": tone,
        "scene": {"location":loc,"time_of_day":tod,"lighting":light,"props":props,"ambient":amb},
        "character_set": team,
        "final_prompt": final
    }
    return final, prompt_json

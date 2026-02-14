import textwrap, random

def build_veo_prompt(topic: str, creature="Yeti"):
    # You can set creature to "Yeti", "Bigfoot", or "Cat (bipedal, hyper-real)"
    envs = [
        "A foggy mountain trail at sunrise. Mist rolls off the trees; every breath the {creature} exhales forms visible vapor. The path is rocky with small puddles reflecting pink light. You hear birds chirping faintly, wind howling in the distance, and occasional gravel crunches as they climb.",
        "A neon-soaked alley after rain. Puddles mirror sign flicker; distant traffic hum. Drips from fire escape punctuate the soundscape as the {creature} speed-walks toward the camera.",
        "A cluttered desk by a city window. Train rumbles far below; keyboard clacks; headset cable sways as the {creature} power-records in a single take.",
    ]
    cams = [
        "Handheld vlog, selfie-stick gripped tightly in the {creature}’s paw-hand. Natural sway with breath; occasional micro-focus hunts.",
        "Chest-mounted camera with authentic stride bounce; lens smears from sweat; occasional tilt corrections.",
        "Phone-in-hand vertical capture; deliberate whip-pans; slight rolling-shutter warble during quick moves.",
    ]
    env = random.choice(envs).format(creature=creature)
    cam = random.choice(cams).format(creature=creature)

    dialogue_lines = [
        f'{creature} (out of breath, to camera): "If a fucking {topic.split()[0]} can trend today, you can finish your shit. Move!"',
        f'{creature} (amped whisper): "Eight seconds. No bullshit. Do the damn thing."',
        f'{creature} (yelling over wind): "Stop scrolling. Do one hard rep. Right fucking now."',
    ]
    dialogue = random.choice(dialogue_lines)

    prompt = f"""Environment • {env}
Camera • {cam}
Character • {creature}: hyper-real, human-like facial expression; natural fur/skin physics; no cartoon traits.
Action • Tie directly to today’s trend: {topic}. Perform a micro-challenge on camera (8 seconds total).
Dialogue (≤ 8 s, explicit) • {dialogue}
Artifacts • Real handheld jitter; subtle mic peak on loud words; light leaks; moisture/fog on frame edges.
Vibe • Hyper-realistic cinematic vlog; gritty; motivational absurdity; relatable exhaustion.
Duration • 8 seconds exact.
Orientation • 9:16 vertical."""
    return textwrap.dedent(prompt).strip()

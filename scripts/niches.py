# scripts/niches.py
from typing import Dict, List


NICHES: Dict[str, Dict] = {
    "cryptid_vlog": {
        "display_name": "Yeti & Bigfoot POV",
        "tone": "sweaty handheld survival vlog, explicit, exhausted motivation",
        "generation_model": "veo3-or-sora2",  # your rule: ONLY these models
        "sponsor_angles": ["energy drink", "outdoor gear", "thermal wear"],
    },
    "ai_cars": {
        "display_name": "Outlaw Future Cars",
        "tone": "illegal street aero kits, neon garage, cyber muscle car POV",
        "generation_model": "veo3-or-sora2",
        "sponsor_angles": ["tire brand", "aftermarket lighting", "wrap shops"],
    },
    "ai_cooking": {
        "display_name": "Midnight Sink Meals",
        "tone": "microwave chef, broke but ridiculous flavors, ASMR grease sizzle",
        "generation_model": "veo3-or-sora2",
        "sponsor_angles": ["hot sauce", "meal prep bowls", "cheap protein snacks"],
    },
    "ai_asmr": {
        "display_name": "Apocalypse ASMR",
        "tone": "soft whispers, post-collapse cozy survival, mic brushing, matches",
        "generation_model": "veo3-or-sora2",
        "sponsor_angles": ["sleep aids", "ambient audio apps"],
    },
    "climate_watch": {
        "display_name": "Planet is Glitching",
        "tone": "calm urgent dispatches, extreme sky events, lightning walls, aurora anomalies",
        "generation_model": "veo3-or-sora2",
        "sponsor_angles": ["eco orgs", "prep gear", "weather radios"],
    },
    # …add more verticals here over time
}


def list_niches() -> List[Dict]:
    out = []
    for key, data in NICHES.items():
        out.append(
            {
                "id": key,
                "display_name": data["display_name"],
                "tone": data["tone"],
                "model": data["generation_model"],
                "sponsor_angles": data["sponsor_angles"],
            }
        )
    return out


def get_niche(key: str) -> Dict:
    return NICHES.get(key, {})

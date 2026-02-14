# scripts/sponsor_pitch.py
def generate_brand_pitches(niche: str):
    """
    Returns a list of 'brands we should pitch' for this niche.
    Later we’ll generate actual outreach copy.
    """
    mock = [
        {
            "brand_name": "EnergyBlast",
            "pitch_email": "collabs@energyblast.example",
            "fit_reason": "High-intensity, night-shift, grind aesthetic.",
            "status": "not_contacted",
        },
        {
            "brand_name": "UrbanRugged Wear",
            "pitch_email": "marketing@urbanrugged.example",
            "fit_reason": "Matches gritty street/cryptid survival vibe.",
            "status": "not_contacted",
        },
    ]
    return mock

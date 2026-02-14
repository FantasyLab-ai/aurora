import re, json
from scripts.llm_client import use_provider, ollama, groq

BASE_TAGS={
 "Cute Pet Compilation":["#pets","#cute","#animals","#shorts","#foryou","#petsoftiktok"],
 "Funny Animal Moments":["#funny","#animals","#fails","#comedy","#shorts","#viral"],
 "Pet Reactions":["#petreactions","#cats","#dogs","#shorts","#fyp"],
 "Wildlife Facts":["#wildlife","#facts","#nature","#learn","#shorts"],
 "Wildlife Conservation":["#conservation","#wildlife","#nature","#shorts"],
 "Nature Beauty":["#nature","#beautiful","#scenery","#shorts"],
 "Nature Sounds":["#naturesounds","#asmr","#relax","#shorts"],
 "Fantasy Creatures":["#fantasy","#yeti","#mythic","#shorts"],
 "Outdoor Adventure":["#outdoors","#hiking","#adventure","#shorts"],
 "Tech News":["#tech","#news","#ai","#shorts"],
 "Gaming Moments":["#gaming","#clips","#shorts"]
}

def _heuristic_caption(trend, prompt, characters, m):
    loc=""
    m_env=re.search(r"Environment\s*\n([^\n]+)",prompt or "",re.I)
    if m_env: loc = (m_env.group(1) or "").split(",")[0].strip()
    who=", ".join([c for c in (characters or []) if c])
    bits=[trend, f"{who} in {loc}" if who and loc else who or loc]
    bits=[b for b in bits if b]
    cap=" • ".join(bits) or trend
    if m and m.get("fps",0)>=24: cap+=" • cinematic 24fps vibe"
    return cap[:120].strip()

def _heuristic_hashtags(trend, prompt, characters, m, max_n=12):
    base=BASE_TAGS.get(trend,["#shorts"])
    add=[]
    for k in (characters or []):
        t=re.sub(r"[^A-Za-z0-9]","",k).lower()
        if t: add.append("#"+t)
    if m and m.get("width",0)>=1280: add.append("#hd")
    tags=[]; seen=set()
    for t in base+add:
        low=t.lower()
        if low not in seen:
            seen.add(low); tags.append(t)
        if len(tags)>=max_n: break
    return tags

def generate_caption_hashtags(trend, prompt, characters, metrics):
    prov=use_provider()
    if prov=="none":
        return _heuristic_caption(trend,prompt,characters,metrics), _heuristic_hashtags(trend,prompt,characters,metrics)
    ask=f"""You are a social video copywriter. Produce JSON with keys:
"caption" (<=120 chars) and "hashtags" (array of 6-12 tags starting with #, all lowercase).
Context:
trend={trend}
characters={characters}
metrics={{'fps':{metrics.get('fps',0)},'dur':{metrics.get('duration_s',0):.2f}}}
prompt:
{prompt}
Return ONLY JSON."""
    try:
        text=groq(ask, max_tokens=160) if prov=="groq" else ollama(ask, max_tokens=160)
        j=json.loads(text)
        cap=j.get("caption") or _heuristic_caption(trend,prompt,characters,metrics)
        tags=j.get("hashtags") or _heuristic_hashtags(trend,prompt,characters,metrics)
        tags=[t if t.startswith("#") else "#"+t.lstrip() for t in tags][:12]
        return cap[:120], tags
    except Exception:
        return _heuristic_caption(trend,prompt,characters,metrics), _heuristic_hashtags(trend,prompt,characters,metrics)

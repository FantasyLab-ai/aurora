import re, time, requests, xml.etree.ElementTree as ET
UA={"User-Agent":"FantasyAI/1.0 (+local)"}

def _uniq(seq):
    seen=set(); out=[]
    for s in seq:
        s=(s or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    return out

def _wiki_top():
    u="https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/today"
    r=requests.get(u,headers=UA,timeout=8); r.raise_for_status()
    out=[]
    for item in r.json().get("items",[]):
        for a in item.get("articles",[]):
            t=a.get("article","").replace("_"," ")
            if t and not re.search(r"^Main Page$|Special:|404",t): out.append(t)
    return out

def _reddit_all():
    u="https://www.reddit.com/r/all/top/.json?t=day&limit=50"
    r=requests.get(u,headers=UA,timeout=8); r.raise_for_status()
    return [c["data"]["title"] for c in r.json()["data"]["children"]]

def _hn():
    u="https://news.ycombinator.com/rss"
    r=requests.get(u,headers=UA,timeout=8); r.raise_for_status()
    root=ET.fromstring(r.text)
    return [i.findtext("title") for i in root.findall("./channel/item")]

def _gn():
    u="https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    r=requests.get(u,headers=UA,timeout=8); r.raise_for_status()
    root=ET.fromstring(r.text)
    return [i.findtext("title") for i in root.findall("./channel/item")]

def free_trends_now(max_items=80):
    parts=[]
    for f in (_wiki_top,_reddit_all,_hn,_gn):
        try: parts+=f()
        except Exception: pass
        time.sleep(0.2)
    return _uniq(parts)[:max_items]

THEMES=["Cute Pet Compilation","Funny Animal Moments","Pet Reactions",
        "Wildlife Facts","Wildlife Conservation","Nature Beauty","Nature Sounds",
        "Fantasy Creatures","Outdoor Adventure","Tech News","Gaming Moments"]

RULES=[
    (r"\b(cat|dog|pet|kitten|puppy|hamster|parrot)\b","Cute Pet Compilation"),
    (r"\b(funny|fail|compilation|bloopers)\b.*\b(cat|dog|animal|pet)\b","Funny Animal Moments"),
    (r"\b(reaction|reacts)\b.*\b(cat|dog|pet|animal)\b","Pet Reactions"),
    (r"\b(wildlife|conservation|endangered|habitat)\b","Wildlife Conservation"),
    (r"\b(wildlife|bear|animal facts|facts)\b","Wildlife Facts"),
    (r"\b(nature|sunset|mountain|beauty|scenery)\b","Nature Beauty"),
    (r"\b(nature sounds|rain|forest sounds|ambience|asmr)\b","Nature Sounds"),
    (r"\b(dragon|yeti|mythic|fantasy)\b","Fantasy Creatures"),
    (r"\b(hike|trail|camp|outdoor|backpacking|overland)\b","Outdoor Adventure"),
    (r"\b(ai|chip|gpu|iphone|android|tesla|open-source|startup|apple|google|microsoft)\b","Tech News"),
    (r"\b(gaming|esports|fps|rpg|fortnite|minecraft|roblox)\b","Gaming Moments"),
]

def choose_theme_from_free(titles):
    scores={t:0 for t in THEMES}
    for title in titles:
        L=title.lower()
        for pat,theme in RULES:
            if re.search(pat,L): scores[theme]+=1
    best=max(scores,key=scores.get)
    return best if scores[best]>0 else "Cute Pet Compilation"

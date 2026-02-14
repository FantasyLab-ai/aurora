


_SENSITIVE_PATTERNS = (
    r"\bshooting\b", r"\bterror\b", r"\bsuicide\b", r"\bmurder\b",
    r"\bdead\b", r"\bdeath\b", r"\bviolence\b", r"\bwar\b",
    r"\bbomb\b", r"\bhostage\b"
)
def _topic_guard(topic: str) -> str:
    import re
    tl = (topic or "").lower()
    for pat in _SENSITIVE_PATTERNS:
        if re.search(pat, tl):
            return "Cute Pet Compilation"
    return topic

import os, logging, random, re
from scripts.trends_free import free_trends_now
from scripts.agents import prompt_agent as pa
from scripts.pipeline import generate_veo_video_cdp, PromptGeneratorStructured
from scripts.post_ai import postprocess_after_render
from scripts.agents.naturalizer import rewrite_dialogue
from scripts.agents.template_v2 import render_topic_prompt
from scripts.config import cfg_get
from scripts.learners.bandit import ThompsonBandit
from scripts.agents.hook_agent import make_hook, inject_hook
from scripts.learners.prompt_scorer_impl import PromptScorer

def _select_prompt_pro(topic, team):
    K = int(cfg_get('PROMPT_CANDIDATES', 3))
    sc = PromptScorer()
    texts=[]; scores=[]
    for _ in range(max(1,K)):
        ptxt, pjson = render_topic_prompt(topic, team)
        sscore = sc.predict(pjson)
        texts.append(ptxt); scores.append(sscore)
    logging.info('🧪 Generated %d candidates (scored), best=%.2f', len(texts), max(scores) if scores else 0)
    # Thompson bandit pick
    bandit = ThompsonBandit()
    ctx = f"{topic}|{','.join(team)}"
    idx = bandit.select(ctx, scores)
    # AB split chance: pick second-best sometimes for exploration
    try:
        ab = float(cfg_get('AB_SPLIT_RATE', 0.0))
    except Exception:
        ab = 0.0
    if ab>0 and __import__('random').random()<ab and len(texts)>1:
        # choose next-best by score different from bandit idx
        order = sorted(range(len(texts)), key=lambda i: scores[i], reverse=True)
        for j in order:
            if j!=idx: idx=j; break
        logging.info('🔀 AB split triggered.')
    return texts[idx]


try:
    from scripts.learners.reward_update import update_from_public
except Exception:
    def update_from_public():
        return 0

_pg = PromptGeneratorStructured()
_scorer = PromptScorer()
try:
    _scorer.maybe_train_from_csv()
except Exception:
    pass


def _pick_topic_raw():
    items = free_trends_now()
    for t in items:
        tt = (t or '').strip()
        # Keep it reasonable for a title
        if 3 <= len(tt) <= 140:
            return tt
    return 'Trending Now'
    try:
        raw = free_trends_now()
        # prefer concise, non-NSFW headlines
        block = re.compile(r"(nsfw|porn|gore|beheading|suicide|self-harm|child|explicit)", re.I)
        cand = [x.strip() for x in raw if 8 <= len(x.strip()) <= 120 and not block.search(x)]
        t = cand[0] if cand else (raw[0] if raw else "Trending Now")
        logging.info("📰 topic source=free(raw) → %s", t); return t
    except Exception as e:
        logging.warning("free topic failed: %s", e)
        return "Trending Now"

_TEAMS = {
  "comedy": [("Yeti A","Kitty A"),("Kitty A","Kitty B"),("Yeti B","Kitty A")],
  "awe":    [("Yeti A","Bear A"),("Bear B","Kitty B")],
  "docu":   [("Bear B","Kitty B"),("Yeti A","Bear A")]
}
def _mood(trend):
    t = trend.lower()
    if any(k in t for k in ("funny","cute","reaction")): return "comedy"
    if any(k in t for k in ("nature","wildlife")): return "awe"
    return "docu"

def _pick_team(trend):
    m = _mood(trend)
    team = random.choice(_TEAMS[m])
    logging.info("🧍 Team chosen (%s): %s", m, list(team))
    return list(team)

def _select_prompt(trend, team):
    # team like ["Yeti B","Kitty A"] → generator expects character names
    chars = [c.split()[0] for c in (team or ["Yeti","Kitty"])] or ["Yeti","Kitty"]
    cands = []
    for _ in range(2):  # generate 2; bump to 3-4 if you want more exploration
        ptxt, pjson = _pg.generate_enhanced_prompt(trend, character_set=chars)
        score = _scorer.predict(pjson)
        cands.append((score, ptxt, pjson))
    cands.sort(key=lambda x: x[0], reverse=True)
    logging.info("🧪 Generated %d candidates (scored), best=%.2f", len(cands), cands[0][0])
    return cands[0][1]





def run_once():
    try:
        import subprocess, sys
        subprocess.run([sys.executable, 'tools/build_metrics_csv.py'], check=False)
    except Exception as e:
        logging.warning('metrics csv build failed: %s', e)

    try:
        n=update_from_public();
        logging.info('♻️ refreshed trend scores from public metrics (%s buckets)', n)
    except Exception as e:
        logging.warning('reward update failed: %s', e)
    topic = _pick_topic_raw()
    team  = _pick_team(topic)
    final_prompt = _select_prompt_pro(topic, team)
    # micro-rewrite for natural vlog style (no humans, hyper-real)
    if cfg_get('MICRO_REWRITE_ON', True):
        try:
            final_prompt = rewrite_dialogue(final_prompt, topic)
        except Exception as _e:
            logging.warning('micro rewrite skipped: %s', _e)
    logging.info("📝 Final prompt\n%s", final_prompt)

    video_path = generate_veo_video_cdp(final_prompt)
    try:
        postprocess_after_render(video_path, topic, team, final_prompt)
        logging.info("📝 Saved caption/hashtags/metrics alongside output.")
    except Exception as e:
        logging.warning("postprocess failed: %s", e)

if __name__ == "__main__":
    random.seed(42)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    run_once()
from __future__ import annotations
import hashlib, math, random
from typing import Iterable, Dict, Tuple
from scripts.metrics import get_stats  # your existing DB-backed stats

def _stats_map(cands: Iterable[str]) -> Dict[str, Tuple[int,int]]:
    want = {hashlib.sha256(c.encode()).hexdigest()[:12]: c for c in cands}
    m = {h:(0,0) for h in want}
    for h, w, l in get_stats():
        if h in m: m[h] = (w, l)
    return m

def pick_ucb1(cands: Iterable[str], total_plays: int | None = None) -> str:
    cands = list(cands); m = _stats_map(cands)
    if not cands: raise ValueError("no candidates")
    # if unseen, explore first
    for i, c in enumerate(cands):
        h = hashlib.sha256(c.encode()).hexdigest()[:12]
        if sum(m[h]) == 0: return c
    N = total_plays or sum(sum(x) for x in m.values()) or 1
    best, best_score = None, -1
    for c in cands:
        h = hashlib.sha256(c.encode()).hexdigest()[:12]
        w, l = m[h]
        n  = max(1, w + l)
        p  = w / n
        ucb = p + math.sqrt(2 * math.log(N) / n)
        if ucb > best_score: best, best_score = c, ucb
    return best or cands[0]

def pick_epsilon_greedy(cands: Iterable[str], eps: float = 0.2) -> str:
    cands = list(cands); m = _stats_map(cands)
    if not cands: raise ValueError("no candidates")
    if random.random() < eps:
        return random.choice(cands)
    # exploit: pick highest empirical mean
    def mean(c: str):
        h = hashlib.sha256(c.encode()).hexdigest()[:12]; w, l = m[h]; n = w + l
        return (w / n) if n else 0.0
    return max(cands, key=mean)

# /scripts/guardrails.py
from __future__ import annotations
from typing import List, Dict

_FORBIDDEN = [
    "official trailer", "full movie", "leaked footage",
    "copyrighted soundtrack", "nuketown exploit"
]
_BRAND_TERMS = ["disney", "marvel", "netflix", "hbo", "pixar", "warner bros"]
_SENSITIVE = ["graphic violence", "hate speech", "self-harm"]

def check_content(title:str, caption:str, script_text:str="") -> List[Dict[str,str]]:
    text = " ".join([(title or ""), (caption or ""), (script_text or "")]).lower()
    issues=[]
    for w in _FORBIDDEN:
        if w in text: issues.append({"type":"copyright", "match":w})
    for b in _BRAND_TERMS:
        if f" {b} " in f" {text} ": issues.append({"type":"brand", "match":b})
    for s in _SENSITIVE:
        if s in text: issues.append({"type":"safety", "match":s})
    # length check (platform-friendly)
    if caption and len(caption) > 2200:
        issues.append({"type":"length", "match":"caption>2200"})
    return issues

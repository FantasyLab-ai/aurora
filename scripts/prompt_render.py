from __future__ import annotations
import os, random, yaml
from jinja2 import Template

CONF = os.path.join(os.path.dirname(__file__), "..", "config", "prompt_templates.yaml")

def render_candidates(topic: str, k: int = 3, overrides: dict | None = None):
    with open(CONF, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    defaults = cfg.get("defaults", {})
    if overrides: defaults.update(overrides)
    outs = []
    for raw in cfg.get("templates", []):
        t = Template(raw)
        outs.append(t.render(topic=topic, **defaults).strip())
    random.shuffle(outs)
    return outs[:k]

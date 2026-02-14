from __future__ import annotations
import subprocess, json, shutil, tempfile, os
from typing import Dict

def _probe(path: str) -> Dict:
    if not shutil.which("ffprobe"):
        return {"warning":"ffprobe not found"}
    cmd = ["ffprobe","-v","quiet","-print_format","json","-show_format","-show_streams",path]
    j = subprocess.check_output(cmd, text=True)
    return json.loads(j)

def _black_ratio(path: str) -> float | None:
    if not shutil.which("ffmpeg"): return None
    cmd = ["ffmpeg","-i",path,"-vf","blackdetect=d=0.3:pic_th=0.98","-f","null","-"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        txt = out.stderr or out.stdout
        # crude: count detected intervals
        hits = txt.count("blackdetect")
        return min(1.0, hits * 0.05)  # heuristic
    except Exception:
        return None

def quality_ok(path: str, min_s=8, max_s=15, min_w=720) -> tuple[bool,str]:
    info = _probe(path)
    if "warning" in info: return True, info["warning"]  # skip checks if no ffprobe
    # duration
    try:
        dur = float(info["format"]["duration"])
        if not (min_s <= dur <= max_s):
            return False, f"duration {dur:.1f}s outside [{min_s},{max_s}]"
    except Exception:
        pass
    # width
    try:
        v = next(s for s in info["streams"] if s.get("codec_type")=="video")
        if int(v.get("width", 0)) < min_w:
            return False, f"width {v.get('width')} < {min_w}"
    except Exception:
        pass
    # black frames heuristic
    br = _black_ratio(path)
    if br is not None and br > 0.25:
        return False, f"black-frame ratio too high ~{br:.2f}"
    return True, "ok"

from __future__ import annotations
import os, subprocess, shutil, tempfile, time
from PIL import Image, ImageDraw, ImageFont

def extract_midframe(video_path: str, out_png: str) -> str:
    if not shutil.which("ffmpeg"): return ""
    tmp = out_png
    cmd = ["ffmpeg","-y","-ss","00:00:01.0","-i",video_path,"-frames:v","1",tmp]
    subprocess.run(cmd, check=True)
    return tmp

def make_thumbnail(video_path: str, title: str, out_png: str) -> str:
    png = extract_midframe(video_path, out_png)
    if not png: return ""
    im = Image.open(png).convert("RGB")
    W,H = im.size
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default()
    # semi-transparent bar
    bar_h = int(0.22*H)
    overlay = Image.new("RGBA", (W, bar_h), (0,0,0,180))
    im.paste(overlay, (0, H-bar_h), overlay)
    # title text
    draw = ImageDraw.Draw(im)
    draw.text((int(0.05*W), int(H-bar_h+0.05*H)), title[:100], font=font, fill=(255,255,255))
    im.save(out_png)
    return out_png

import subprocess, json, pathlib, os
from PIL import Image, ImageDraw, ImageFont

def _ffprobe_dur(p):
    r = subprocess.run(["ffprobe","-v","error","-print_format","json","-show_format",p],
                       capture_output=True, text=True, check=True)
    dur=float(json.loads(r.stdout)["format"].get("duration",0.0) or 0.0)
    return max(0.0, dur)

def make_thumbnail(video_path:str, title:str=""):
    p=pathlib.Path(video_path)
    dur=_ffprobe_dur(str(p)) or 8.0
    ts = max(0.0, dur*0.3)
    out = p.with_suffix(".jpg")
    subprocess.run(["ffmpeg","-y","-ss",f"{ts:.2f}","-i",str(p),"-frames:v","1","-q:v","2",str(out)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Optional overlay text if Pillow exists
    try:
        im=Image.open(out).convert("RGBA")
        draw=ImageDraw.Draw(im)
        w,h=im.size
        box_h=int(h*0.15)
        overlay=Image.new("RGBA",(w,box_h),(0,0,0,160))
        im.paste(overlay,(0,h-box_h),overlay)
        font=None
        try: font=ImageFont.truetype("DejaVuSans-Bold.ttf", int(box_h*0.45))
        except: font=ImageFont.load_default()
        draw.text((int(w*0.03), h-box_h+int(box_h*0.25)), (title or "")[:80], fill=(255,255,255,230), font=font)
        im.convert("RGB").save(out, "JPEG", quality=92)
    except Exception:
        pass
    return str(out)

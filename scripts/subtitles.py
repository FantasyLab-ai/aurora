import re, json, subprocess, os, pathlib

def _dialogue_block_from_prompt(prompt:str):
    # Grab "Dialogue (XXs)" section until next blank-line header (Artifacts|Vibe|Camera|Environment)
    m = re.search(r"(?s)Dialogue\s*\((\d+)\s*s\)\s*(.+?)(?:\n\n(?:Artifacts|Vibe|Camera|Environment)\b|\Z)", prompt)
    if not m: return 0, []
    dur = int(m.group(1))
    lines = [ln.strip() for ln in m.group(2).splitlines() if ln.strip()]
    # Only keep "Speaker: text" lines
    out=[]
    for ln in lines:
        if ": " in ln:
            spk, txt = ln.split(": ",1)
            spk = re.sub(r"^[^\w]*","",spk).strip()   # drop emoji
            out.append((spk or "Speaker", txt.strip()))
    return dur if dur>0 else max(8, 2*len(out)), out

def build_srt_from_prompt(prompt:str, out_path:str):
    total, lines = _dialogue_block_from_prompt(prompt or "")
    if not lines: return None
    per = max(1, total // max(1,len(lines)))
    def fmt(ts):
        h=ts//3600; m=(ts%3600)//60; s=ts%60
        return f"{h:02}:{m:02}:{s:02},000"
    t=0; idx=1
    chunks=[]
    for spk, txt in lines:
        start=t; end=min(total, t+per)
        chunks.append(f"{idx}\n{fmt(start)} --> {fmt(end)}\n{spk}: {txt}\n")
        t=end; idx+=1
    pathlib.Path(out_path).write_text("\n".join(chunks), encoding="utf-8")
    return out_path

def burn_in(video_path:str, srt_path:str):
    out = str(pathlib.Path(video_path).with_suffix(".cc.mp4"))
    # Rely on ffmpeg subtitles filter; if libass not present, just return sidecar SRT
    cmd = ["ffmpeg","-y","-i",video_path,"-vf",f"subtitles={srt_path}:force_style='Fontsize=20'","-c:a","copy",out]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out
    except Exception:
        return None

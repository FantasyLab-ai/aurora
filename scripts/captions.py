from __future__ import annotations
import os, shutil, subprocess, tempfile, time, json

def _has_whisper():
    try:
        import whisper  # type: ignore
        return True
    except Exception:
        return False

def _extract_audio(video: str, wav: str):
    if not shutil.which("ffmpeg"): return False
    cmd = ["ffmpeg","-y","-i",video,"-vn","-ac","1","-ar","16000",wav]
    return subprocess.run(cmd, capture_output=True).returncode == 0

def make_captions(video_path: str, out_srt: str, model: str = "base") -> str:
    if not _has_whisper(): return ""
    import whisper  # type: ignore
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        if not _extract_audio(video_path, wav): return ""
        m = whisper.load_model(model)
        res = m.transcribe(wav, task="transcribe", verbose=False)
        # Write simple SRT
        def fmt(t): 
            ms = int((t - int(t))*1000); s = int(t)%60; m = (int(t)//60)%60; h=int(t)//3600
            return f"{h:02}:{m:02}:{s:02},{ms:03}"
        lines=[]; idx=1
        for seg in res.get("segments", []):
            lines += [str(idx), f"{fmt(seg['start'])} --> {fmt(seg['end'])}", seg['text'].strip(), ""]
            idx += 1
        with open(out_srt,"w",encoding="utf-8") as f: f.write("\n".join(lines))
    return out_srt

def burn_in(video_path: str, srt_path: str, out_mp4: str) -> str:
    if not shutil.which("ffmpeg") or not os.path.exists(srt_path): return ""
    cmd = ["ffmpeg","-y","-i",video_path,"-vf",f"subtitles={srt_path}:force_style='Fontsize=20,PrimaryColour=&HFFFFFF&'",
           "-c:a","copy", out_mp4]
    try:
        subprocess.run(cmd, check=True)
        return out_mp4
    except Exception:
        return ""

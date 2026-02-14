import subprocess, pathlib

def _mk(in_path, w, h, out_suffix):
    p=pathlib.Path(in_path)
    out=str(p.with_name(p.stem + out_suffix).with_suffix(".mp4"))
    # scale to fit, then pad
    vf=f"scale=w={w}:h={h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    cmd=["ffmpeg","-y","-i",str(p),"-vf",vf,"-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart",out]
    subprocess.run(cmd, check=True)
    return out

def make_variants(in_path:str):
    v916=_mk(in_path,1080,1920,"_9x16")
    v11=_mk(in_path,1080,1080,"_1x1")
    return v916, v11

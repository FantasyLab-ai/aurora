# -*- coding: utf-8 -*-
"""
Engines
-------
Single switch to route rendering to Veo (browser/CDP) or future Sora integration.

You already have a working browser-driven Veo flow. Wire that function here
(so callers just pass `engine="veo"` and a `prompt`).

Usage:
    from scripts.engines import render_video
    result = render_video(engine="veo", prompt=my_prompt, out_dir="/path/to/outputs")

Return shape:
    {
      "engine": "veo",
      "ok": True/False,
      "prompt": "...",
      "output_path": "/path/to/file.mp4" or None,
      "error": None or "message"
    }
"""

from typing import Dict, Any, Optional
import os
import subprocess
import tempfile
import json


def _render_with_veo_browser(prompt: str, out_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Option A (recommended): call your existing PY function if you have it, e.g.:
        from scripts.browser_veo import generate
        return generate(prompt, out_dir=out_dir)

    Option B (works now): call your existing CLI that already drives Veo via CDP.
    We write the prompt to a temp file and pass its path via env var or arg if you’ve added support.
    If your current script only accepts --topic, it will ignore this. Consider adding a --prompt_path.
    """

    out_dir = out_dir or os.path.expanduser("~/Desktop/FantasyAI/outputs")
    os.makedirs(out_dir, exist_ok=True)

    # Write prompt to a temp file so your run_end_to_end.py can read it (if you add support)
    tmp = tempfile.NamedTemporaryFile(prefix="veo_prompt_", suffix=".txt", delete=False)
    tmp.write(prompt.encode("utf-8"))
    tmp.flush()
    tmp.close()

    # --- Adjust this block to match your runner ---
    # If your script supports --prompt_path, use that:
    cmd = [
        "python",
        os.path.expanduser("~/Desktop/FantasyAI/run_end_to_end.py"),
        "--prompt_path", tmp.name
    ]

    # Fallback (if you *only* have --topic): we pass a minimal topic so pipeline still runs.
    # Comment the above `cmd` and uncomment below if needed.
    # cmd = [
    #     "python",
    #     os.path.expanduser("~/Desktop/FantasyAI/run_end_to_end.py"),
    #     "--topic", "autogen niche prompt run"
    # ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        stdout, stderr = proc.stdout, proc.stderr
        ok = proc.returncode == 0

        # Try to find an output path line your script prints (adjust the key if different)
        output_path = None
        for line in (stdout or "").splitlines():
            if "Video written to:" in line:
                output_path = line.split("Video written to:", 1)[-1].strip()
                break

        return {
            "engine": "veo",
            "ok": ok,
            "prompt": prompt,
            "output_path": output_path,
            "stdout": stdout,
            "stderr": stderr,
            "error": None if ok else (stderr or "veo render failed"),
        }
    except Exception as e:
        return {"engine": "veo", "ok": False, "prompt": prompt, "output_path": None, "error": str(e)}


def _render_with_sora_api(prompt: str, out_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Placeholder for Sora. API access is allow-listed; when you have it, replace this with
    an OpenAI SDK call (duration=8s, aspect 9:16) and download the resulting URL to out_dir.
    """
    return {
        "engine": "sora",
        "ok": False,
        "prompt": prompt,
        "output_path": None,
        "error": "Sora integration not enabled. Replace this stub once you have API access."
    }


def render_video(engine: str, prompt: str, out_dir: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    engine = (engine or "veo").strip().lower()
    if engine == "veo":
        return _render_with_veo_browser(prompt, out_dir=out_dir)
    if engine == "sora":
        return _render_with_sora_api(prompt, out_dir=out_dir)
    return {"engine": engine, "ok": False, "prompt": prompt, "output_path": None, "error": f"Unknown engine: {engine}"}

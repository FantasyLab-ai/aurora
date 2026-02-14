# --- Aurora API (add to studio_api.py) ---
import os
import json
import subprocess
from pathlib import Path
from flask import request, jsonify, send_file, abort

AURORA_ROOT = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE").resolve()
AURORA_PY = AURORA_ROOT / ".venv" / "Scripts" / "python.exe"
AURORA_RUNNER = "scripts.run_aurora_with_steps_1_4"

def _safe_path(p: Path) -> Path:
    p = p.resolve()
    if AURORA_ROOT not in p.parents and p != AURORA_ROOT:
        raise ValueError("Path outside Aurora root")
    return p

@app.route("/aurora/run", methods=["POST"])
def aurora_run():
    """
    Body:
      {
        "dataset": "env_air_quality",
        "seed": 42,
        "also_math": true,
        "math_v2": true,
        "deep_math": true,
        "also_llm": true
      }
    """
    body = request.get_json(force=True, silent=True) or {}
    dataset = body.get("dataset", "env_air_quality")
    seed = int(body.get("seed", 42))

    also_math = bool(body.get("also_math", True))
    math_v2 = bool(body.get("math_v2", True))
    deep_math = bool(body.get("deep_math", True))
    also_llm = bool(body.get("also_llm", True))

    if not AURORA_PY.exists():
        return jsonify({"ok": False, "error": f"Aurora python not found: {AURORA_PY}"}), 500

    cmd = [
        str(AURORA_PY),
        "-m", AURORA_RUNNER,
        "--dataset", dataset,
        "--seed", str(seed),
    ]
    if also_math: cmd.append("--also-math")
    if math_v2: cmd.append("--math-v2")
    if deep_math: cmd.append("--deep-math")
    if also_llm: cmd.append("--also-llm")

    # Ensure working directory is Aurora root so relative paths work
    proc = subprocess.run(
        cmd,
        cwd=str(AURORA_ROOT),
        capture_output=True,
        text=True,
        timeout=60 * 10
    )

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    # Runner prints a JSON blob near the top; try to extract run_dir
    run_dir = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and '"run_dir"' in line:
            try:
                obj = json.loads(line)
                run_dir = obj.get("run_dir")
                break
            except Exception:
                pass

    return jsonify({
        "ok": proc.returncode == 0,
        "dataset": dataset,
        "seed": seed,
        "run_dir": run_dir,
        "stdout": stdout[-8000:],  # keep response bounded
        "stderr": stderr[-8000:],
        "returncode": proc.returncode,
    })

@app.route("/aurora/run_summary", methods=["GET"])
def aurora_run_summary():
    """
    Query: ?run_dir=C:\...\outputs\aurora_dataset_runs\...\

    Returns key json artifacts:
      - _ENGINE_SCORECARD.json
      - deep_math.json
      - llm_narrative_guarded.json
      - _PLOTS_INDEX.json
    """
    run_dir = request.args.get("run_dir")
    if not run_dir:
        return jsonify({"ok": False, "error": "Missing run_dir"}), 400

    rd = _safe_path(Path(run_dir))
    if not rd.exists():
        return jsonify({"ok": False, "error": f"run_dir not found: {rd}"}), 404

    def read_json(name: str):
        p = rd / name
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))

    scorecard = read_json("_ENGINE_SCORECARD.json")
    deep = read_json("deep_math.json")
    narrative = read_json("llm_narrative_guarded.json")
    plots_index = read_json("_PLOTS_INDEX.json")

    return jsonify({
        "ok": True,
        "run_dir": str(rd),
        "scorecard": scorecard,
        "deep_math": deep,
        "llm_narrative_guarded": narrative,
        "plots_index": plots_index,
    })

@app.route("/aurora/plot", methods=["GET"])
def aurora_plot():
    """
    Query: ?run_dir=...&name=forecast.png
    Serves image from run_dir/plots/name
    """
    run_dir = request.args.get("run_dir")
    name = request.args.get("name")
    if not run_dir or not name:
        return jsonify({"ok": False, "error": "Missing run_dir or name"}), 400

    rd = _safe_path(Path(run_dir))
    p = rd / "plots" / name
    if not p.exists():
        abort(404)
    return send_file(str(p), mimetype="image/png")

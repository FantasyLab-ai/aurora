"""
Aurora Studio API — Flask server.

Responsibilities:

  * Serve the frontend at ``GET /``.
  * Expose the assembled artifact state at ``GET /api/state[?run_dir=...]``.
  * List historical runs at ``GET /api/runs``.
  * Trigger a new analysis run at ``POST /api/run``.
  * Accept a dataset upload + auto-run at ``POST /api/upload``.
  * Serve plot images at ``GET /aurora/plot?run_dir=...&name=...``.

This file is intentionally small. All artifact reading lives in
``fantasyai.aurora.state_builder``; running the analyzer lives in
``scripts.run_aurora_with_steps_1_4``. This module is the wiring between them.

To start::

    python studio_api.py
    # or
    python -m studio_api
    # or
    python -m flask --app studio_api run --host 0.0.0.0 --port 8000

then open http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from flask import (
    Flask, abort, jsonify as _flask_jsonify, request, send_file, send_from_directory,
)
from flask_cors import CORS  # type: ignore
from werkzeug.utils import secure_filename


# ---------------------------------------------------------------------------
# JSON sanitization
# ---------------------------------------------------------------------------
# JSON spec rejects NaN/Inf literals AND Python's json.dumps doesn't know how
# to encode numpy/pandas scalars (np.int64, np.float32, pd.Timestamp, …) that
# Aurora's analyzers produce in nearly every artifact. The runner writes those
# JSON-clean (via json.dumps(default=str)), but state_builder loads them back
# as dicts and then attaches MORE numpy-typed fields (matrix-profile motifs,
# physics priors, ML ensembles, replication deltas). Without a recursive
# coercion at the response boundary, /api/state 500s on any connector that
# emits ints (USGS magnitudes, OpenML class counts, World Bank year columns).
#
# This is the single chokepoint. Every JSON response on the server flows
# through the local ``jsonify(...)`` below, which calls _sanitize_for_json
# first. If you add a new endpoint and forget to use the local jsonify, you
# reintroduce the bug — there is one of those (api_health was using flask
# jsonify directly); fixed alongside this rewrite.
#
# Coercion rules (specific → general):
#   1.  None / str / bool / int                            ← native, pass through
#   2.  Python float                                       ← strip NaN/Inf, else pass
#   3.  numpy scalar (np.int64, np.float64, np.bool_, …)    ← .item() then recurse
#   4.  numpy ndarray                                      ← tolist() then recurse
#   5.  pandas Timestamp                                   ← isoformat (NaT → None)
#   6.  pandas NaT / Timedelta                             ← None / total_seconds
#   7.  pandas Series / DataFrame                          ← tolist / to_dict
#   8.  datetime / date                                    ← isoformat
#   9.  pathlib.Path                                       ← str
#  10.  set / frozenset                                    ← list (recurse)
#  11.  dict                                               ← recurse keys + values
#  12.  list / tuple                                       ← recurse
#  13.  fallback                                           ← vars() if has __dict__,
#                                                            else str(); else None
# ---------------------------------------------------------------------------

# Lazy-imported to keep the server importable when numpy/pandas are missing.
_NUMPY = None
_PANDAS = None


def _lazy_numpy():
    global _NUMPY
    if _NUMPY is None:
        try:
            import numpy as _np
            _NUMPY = _np
        except Exception:  # pragma: no cover
            _NUMPY = False
    return _NUMPY


def _lazy_pandas():
    global _PANDAS
    if _PANDAS is None:
        try:
            import pandas as _pd
            _PANDAS = _pd
        except Exception:  # pragma: no cover
            _PANDAS = False
    return _PANDAS


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively coerce ``obj`` into JSON-native Python types.

    Never raises. If a leaf can't be coerced, falls back to ``str(obj)``;
    truly hostile inputs (raise on str()) become None.
    """
    # 1. Cheap native checks (most common — keep first for speed).
    if obj is None or isinstance(obj, (str, bool)):
        return obj

    # 2. Numpy scalar BEFORE int/float — np.int64 IS a subclass of int on many
    #    platforms but flask's encoder still rejects it.
    np = _lazy_numpy()
    if np and isinstance(obj, np.generic):
        # 0-d numpy scalar → Python native via .item().
        try:
            v = obj.item()
        except Exception:
            return str(obj)
        # .item() may itself return a float NaN; recurse to canonicalise.
        return _sanitize_for_json(v)

    # 3. Native int / float (int after numpy.generic check above).
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    # 4. Numpy array → list, recursing.
    if np and isinstance(obj, np.ndarray):
        try:
            return _sanitize_for_json(obj.tolist())
        except Exception:
            return None

    # 5–7. Pandas — Timestamp / NaT / Timedelta / Series / DataFrame.
    pd = _lazy_pandas()
    if pd:
        if isinstance(obj, pd.Timestamp):
            try:
                return None if pd.isna(obj) else obj.isoformat()
            except Exception:
                return str(obj)
        # NaT comparison via identity-or-isna; pd.NaT is a singleton.
        try:
            if obj is pd.NaT or (hasattr(obj, "__class__") and
                                  obj.__class__.__name__ == "NaTType"):
                return None
        except Exception:
            pass
        if isinstance(obj, pd.Timedelta):
            try:
                return obj.total_seconds()
            except Exception:
                return str(obj)
        if isinstance(obj, pd.Series):
            try:
                return _sanitize_for_json(obj.tolist())
            except Exception:
                return None
        if isinstance(obj, pd.DataFrame):
            try:
                return _sanitize_for_json(obj.to_dict(orient="records"))
            except Exception:
                return None

    # 8. datetime / date.
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()

    # 9. Path.
    if isinstance(obj, Path):
        return str(obj)

    # 10. Sets.
    if isinstance(obj, (set, frozenset)):
        return [_sanitize_for_json(v) for v in obj]

    # 11. Dict — recurse. JSON keys must be strings; coerce non-string keys.
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str):
                key = k
            elif isinstance(k, (int, float, bool)) and not isinstance(k, bool):
                key = str(k)
            else:
                # Recurse the key first to canonicalise (e.g. np.int64 → int → str).
                key = str(_sanitize_for_json(k))
            out[key] = _sanitize_for_json(v)
        return out

    # 12. List / tuple.
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]

    # 13. Fallback. Many of Aurora's dataclasses live in this branch.
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        try:
            return _sanitize_for_json(obj.to_dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return _sanitize_for_json(vars(obj))
        except Exception:
            pass
    try:
        return str(obj)
    except Exception:  # pragma: no cover
        return None


def jsonify(data: Any = None, **kwargs):
    """Drop-in replacement for flask.jsonify that sanitizes first.

    Accepts the same call shapes flask's jsonify does. We sanitize before
    handing to flask so numpy/pandas types — and NaN/Inf — never reach the
    encoder. This is the single chokepoint for /api/* responses.
    """
    if data is None and kwargs:
        return _flask_jsonify(_sanitize_for_json(kwargs))
    return _flask_jsonify(_sanitize_for_json(data))

# Import the state builder. Defensive: if anything in the package errors on
# import, we still want a working server that returns "not available" rather
# than 500ing on every request.
try:
    from fantasyai.aurora.state_builder import (
        build_state, find_latest_run, list_runs,
    )
    _HAVE_STATE = True
    _STATE_IMPORT_ERROR = ""
except Exception as e:  # pragma: no cover
    _HAVE_STATE = False
    _STATE_IMPORT_ERROR = f"{type(e).__name__}: {e}"

try:
    from fantasyai.aurora.io.loader import load_dataset, LoadError
    _HAVE_LOADER = True
    _LOADER_IMPORT_ERROR = ""
except Exception as e:  # pragma: no cover
    _HAVE_LOADER = False
    _LOADER_IMPORT_ERROR = f"{type(e).__name__}: {e}"

try:
    from fantasyai.aurora.provenance import (
        load_entry as load_provenance_entry,
        build_provenance_for_run,
    )
    _HAVE_PROVENANCE = True
except Exception as e:  # pragma: no cover
    _HAVE_PROVENANCE = False
    load_provenance_entry = None
    build_provenance_for_run = None

try:
    from fantasyai.aurora.explainers import (
        explain_anomaly, explain_regime, explain_causal, explain_physics,
    )
    _HAVE_EXPLAINERS = True
except Exception as e:  # pragma: no cover
    _HAVE_EXPLAINERS = False
    explain_anomaly = explain_regime = explain_causal = explain_physics = None

try:
    from fantasyai.aurora.whatif import (
        InterventionSpec, parse_intervention_text, simulate_intervention,
    )
    _HAVE_WHATIF = True
except Exception as e:  # pragma: no cover
    _HAVE_WHATIF = False
    InterventionSpec = parse_intervention_text = simulate_intervention = None

try:
    from fantasyai.aurora.cache import (
        compute_cache_key, write_cache_key, find_cached_run,
        normalize_flags, list_cache_entries,
    )
    _HAVE_CACHE = True
except Exception as e:  # pragma: no cover
    _HAVE_CACHE = False
    compute_cache_key = write_cache_key = find_cached_run = None
    normalize_flags = list_cache_entries = None

try:
    from fantasyai.aurora.domain_config import (
        get_domain_config, list_domains, apply_domain_to_state,
    )
    _HAVE_DOMAINS = True
except Exception as e:  # pragma: no cover
    _HAVE_DOMAINS = False
    get_domain_config = list_domains = apply_domain_to_state = None

try:
    from fantasyai.aurora.export import (
        export_findings_markdown, export_methods_markdown,
        export_reproducibility_bundle, export_all,
    )
    _HAVE_EXPORT = True
except Exception as e:  # pragma: no cover
    _HAVE_EXPORT = False
    export_findings_markdown = export_methods_markdown = None
    export_reproducibility_bundle = export_all = None

try:
    from fantasyai.aurora.kb import match_finding_to_kb, list_kb_sources
    _HAVE_KB = True
except Exception as e:  # pragma: no cover
    _HAVE_KB = False
    match_finding_to_kb = list_kb_sources = None

try:
    from fantasyai.aurora.symbolic_discovery import discover_relation
    _HAVE_SR = True
except Exception as e:  # pragma: no cover
    _HAVE_SR = False
    discover_relation = None

try:
    from fantasyai.aurora.conservation import detect_invariants
    _HAVE_CONSERVATION = True
except Exception as e:  # pragma: no cover
    _HAVE_CONSERVATION = False
    detect_invariants = None

try:
    from fantasyai.aurora.connectors import (
        list_connectors as list_data_connectors,
        get_connector as get_data_connector,
    )
    _HAVE_CONNECTORS = True
except Exception as e:  # pragma: no cover
    _HAVE_CONNECTORS = False
    list_data_connectors = get_data_connector = None


# ---------------------------------------------------------------------------
# Paths — repo-relative, no hardcoded user dirs.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent

# When running from a worktree (.claude/worktrees/…), prefer outputs in the
# parent (main) repo so the user sees their existing runs immediately.
def _detect_outputs_dir() -> Path:
    if os.environ.get("AURORA_OUTPUTS"):
        return Path(os.environ["AURORA_OUTPUTS"])
    candidates = [
        REPO_ROOT / "outputs" / "aurora_dataset_runs",
    ]
    p = REPO_ROOT
    for _ in range(4):
        p = p.parent
        if p == p.parent:
            break
        candidates.append(p / "outputs" / "aurora_dataset_runs")
    for c in candidates:
        if c.exists() and any(c.iterdir() if c.is_dir() else []):
            return c
    return candidates[0]

FRONTEND_DIR = REPO_ROOT / "frontend"
DEFAULT_OUTPUTS = _detect_outputs_dir()
UPLOADS_DIR = REPO_ROOT / "data" / "uploads"
PYTHON = sys.executable
RUNNER_MODULE = os.environ.get("AURORA_RUNNER", "scripts.run_aurora_with_steps_1_4")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)
CORS(app)  # permissive by default; tighten via env when deploying.


@app.route("/")
def index():
    """Serve the frontend HTML with no-cache headers.

    Aurora is in active development; serving stale JS to a user causes
    silent breakage that's hard to diagnose. We force a fresh load every
    time. Tradeoff: a few extra KB on every page-load.
    """
    html = FRONTEND_DIR / "index.html"
    if html.exists():
        resp = send_file(str(html))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return (
        "<h1>Aurora Studio</h1>"
        f"<p>Frontend not found at <code>{html}</code>.</p>"
        "<p>API is up. Try <a href='/api/health'>/api/health</a>.</p>"
    ), 200


@app.route("/static/<path:filename>")
def static_files(filename: str):
    """Optional bucket for additional static assets if frontend grows."""
    return send_from_directory(str(FRONTEND_DIR), filename)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    info: Dict[str, Any] = {
        "ok": True,
        "have_state_builder": _HAVE_STATE,
        "outputs_dir": str(DEFAULT_OUTPUTS),
        "runner_repo": str(_detect_runner_repo()),
        "runner_module": RUNNER_MODULE,
        "python": PYTHON,
        "ollama_url": DEFAULT_OLLAMA_URL,
        "ollama_model": DEFAULT_OLLAMA_MODEL,
        "ollama_reachable": _ollama_available(DEFAULT_OLLAMA_URL, timeout=1.0),
    }
    if not _HAVE_STATE:
        info["state_import_error"] = _STATE_IMPORT_ERROR
    # If Ollama is reachable, list the models actually available so we can
    # confirm gemma3:12b (or whatever) is present.
    try:
        if info["ollama_reachable"]:
            import requests
            r = requests.get(f"{DEFAULT_OLLAMA_URL.rstrip('/')}/api/tags", timeout=2)
            if r.ok:
                tags = r.json().get("models") or []
                info["ollama_models_available"] = [m.get("name") for m in tags if m.get("name")]
                info["ollama_model_present"] = info["ollama_model"] in info["ollama_models_available"]
    except Exception:
        pass
    return jsonify(info)


@app.route("/api/debug/runner", methods=["GET", "POST"])
def debug_runner():
    """Diagnose why ``/api/run`` and ``/api/upload`` fail without firing the runner.

    Steps performed (each is optional and returns its own ``ok``):
      * resolve which directory we'd run from (``runner_repo``)
      * verify ``scripts/run_aurora_with_steps_1_4.py`` exists there
      * try importing the runner module (catches ImportError early)
      * run ``python -m scripts.run_aurora_with_steps_1_4 --help`` — confirms
        the subprocess invocation succeeds at the entrypoint level
    """
    repo = _detect_runner_repo()
    runner_path = repo / "scripts" / "run_aurora_with_steps_1_4.py"
    out: Dict[str, Any] = {
        "python": PYTHON,
        "runner_module": RUNNER_MODULE,
        "runner_repo": str(repo),
        "runner_file_exists": runner_path.exists(),
        "outputs_dir": str(DEFAULT_OUTPUTS),
        "outputs_dir_exists": DEFAULT_OUTPUTS.exists(),
    }

    # Try a noop import + --help invocation to see what fails first.
    try:
        proc = subprocess.run(
            [PYTHON, "-m", RUNNER_MODULE, "--help"],
            cwd=str(repo), capture_output=True, text=True, timeout=30,
        )
        out["help_returncode"] = proc.returncode
        out["help_stdout_tail"] = (proc.stdout or "")[-1500:]
        out["help_stderr_tail"] = (proc.stderr or "")[-2500:]
        out["help_ok"] = proc.returncode == 0
    except Exception as e:
        out["help_ok"] = False
        out["help_error"] = f"{type(e).__name__}: {e}"

    # Also try importing the actual modules in-process so we can pinpoint missing deps.
    try:
        import importlib
        importlib.import_module("fantasyai")
        out["fantasyai_importable"] = True
    except Exception as e:
        out["fantasyai_importable"] = False
        out["fantasyai_import_error"] = f"{type(e).__name__}: {e}"

    out["ok"] = bool(out.get("runner_file_exists") and out.get("help_ok"))
    return jsonify(out)


# ---------------------------------------------------------------------------
# State (the single endpoint the frontend hits on load)
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    """Return the assembled state for a run.

    Query:
      run_dir : Optional[str]  — explicit run directory; defaults to latest.

    Response (always 200, even when empty):
      { ok: bool, run_dir: str|null, state: dict|null, reason: str|null }
    """
    if not _HAVE_STATE:
        return jsonify({
            "ok": False, "run_dir": None, "state": None,
            "reason": f"state_builder import failed: {_STATE_IMPORT_ERROR}",
        }), 200

    run_dir_arg = request.args.get("run_dir")
    run_id_arg = request.args.get("run_id")
    rd: Path = Path("")
    if run_dir_arg:
        rd = Path(run_dir_arg)
    elif run_id_arg:
        # Look up the registered run's directory.
        try:
            from fantasyai.aurora import run_registry as _reg
            snap = _reg.snapshot(run_id_arg)
            if snap and snap.get("run_dir"):
                rd = Path(snap["run_dir"])
        except Exception:
            rd = Path("")
        if not rd or not rd.exists():
            return jsonify({
                "ok": False, "run_dir": None, "state": None,
                "reason": f"run_id {run_id_arg} not yet has a run_dir",
            }), 200
    else:
        rd = find_latest_run(DEFAULT_OUTPUTS) or Path("")

    if not rd or not rd.exists():
        return jsonify({
            "ok": False, "run_dir": None, "state": None,
            "reason": "no runs yet — trigger one via POST /api/run",
        }), 200

    try:
        state = build_state(rd)
    except Exception as e:
        return jsonify({
            "ok": False, "run_dir": str(rd), "state": None,
            "reason": f"state assembly failed: {type(e).__name__}: {e}",
        }), 200

    # Domain mode (W8): re-skin terminology + reorder priors per the active domain.
    domain_arg = request.args.get("domain")
    if domain_arg and _HAVE_DOMAINS:
        try:
            apply_domain_to_state(state, domain_arg)
        except Exception:
            pass  # never block the response on a domain-rewrite failure

    return jsonify({"ok": True, "run_dir": str(rd), "state": state, "reason": None})


@app.route("/api/domains")
def api_domains():
    """List the supported domain modes for the frontend pill row."""
    if not _HAVE_DOMAINS:
        return jsonify({"ok": False, "domains": []}), 503
    return jsonify({"ok": True, "domains": list_domains()})


# ---------------------------------------------------------------------------
# Decision-contract acquisition (Phase B)
# ---------------------------------------------------------------------------

@app.route("/api/contracts")
def api_contracts():
    """List the 21 default decision contracts.

    Query:
      template_id : Optional[str] — filter to one template (3 results).

    Response: { ok, contracts: [...] }
    """
    try:
        from fantasyai.aurora.decision_contract import (
            list_default_contracts, suggest_contracts_for_template,
        )
    except Exception as e:
        return jsonify({"ok": False, "contracts": [],
                        "error": f"import failed: {e}"}), 200
    template_id = request.args.get("template_id")
    if template_id:
        out = [c.to_dict() for c in suggest_contracts_for_template(template_id)]
    else:
        out = [c.to_dict() for c in list_default_contracts()]
    return jsonify({"ok": True, "contracts": out})


@app.route("/api/contracts/active", methods=["GET", "POST"])
def api_contract_active():
    """Get / set the active decision contract for a run.

    GET  ?run_dir=...                → { ok, contract: <or null> }
    POST { run_dir, contract: {...} } → write active_contract.json + re-render
    """
    try:
        from fantasyai.aurora.decision_contract import (
            DecisionContract, get_default_contract,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"import failed: {e}"}), 500

    run_dir_arg = (request.args.get("run_dir") if request.method == "GET"
                   else (request.get_json(silent=True) or {}).get("run_dir"))
    if not run_dir_arg:
        return jsonify({"ok": False, "error": "run_dir required"}), 400
    rd = Path(run_dir_arg)
    if not rd.exists():
        return jsonify({"ok": False, "error": f"run_dir not found: {rd}"}), 404
    ac_path = rd / "active_contract.json"

    if request.method == "GET":
        if not ac_path.exists():
            return jsonify({"ok": True, "contract": None})
        try:
            return jsonify({"ok": True,
                            "contract": json.loads(ac_path.read_text(encoding="utf-8"))})
        except Exception as e:
            return jsonify({"ok": False, "error": f"read failed: {e}"}), 500

    # POST
    payload = request.get_json(silent=True) or {}
    contract_dict = payload.get("contract") or {}
    contract_id = contract_dict.get("id")
    # Allow shorthand: just pass {"contract_id": "thermal:explore"}.
    if not contract_dict and payload.get("contract_id"):
        c = get_default_contract(payload["contract_id"])
        if c is None:
            return jsonify({"ok": False,
                            "error": f"unknown contract id: {payload['contract_id']}"}), 400
        contract_dict = c.to_dict()
        contract_id = c.id
    if not contract_id:
        return jsonify({"ok": False,
                        "error": "contract.id required"}), 400
    try:
        ac_path.write_text(json.dumps(contract_dict, indent=2),
                            encoding="utf-8")
    except Exception as e:
        return jsonify({"ok": False, "error": f"write failed: {e}"}), 500
    return jsonify({"ok": True, "contract": contract_dict})


@app.route("/api/contracts/infer", methods=["POST"])
def api_contract_infer():
    """Best-guess a contract from a free-text user description.

    Body: { text: str, template_id: Optional[str] }
    Response: { ok, contract: {...}, source: "inferred" }

    The inference is deterministic keyword matching (NOT an LLM). If the
    description matches well-known intent words (avoid/protect/risk →
    avoid_harm; confirm/publish/log → confirm; explore/discover/learn →
    explore), we map to the matching default; otherwise fall back to
    the explore contract for the template.
    """
    try:
        from fantasyai.aurora.decision_contract import (
            suggest_contracts_for_template, get_default_contract,
            ContractClass,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"import failed: {e}"}), 500

    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").lower()
    template_id = (payload.get("template_id") or "").lower() or None

    avoid_kw = ("avoid", "prevent", "protect", "miss", "risk", "danger",
                "harm", "breach", "fail", "alert", "alarm", "downtime",
                "loss", "diagnose")
    confirm_kw = ("confirm", "verify", "publish", "log", "audit", "validate",
                  "certify", "register")
    explore_kw = ("explore", "discover", "learn", "understand", "characterise",
                  "characterize", "survey")

    cls = ContractClass.EXPLORE
    if any(k in text for k in avoid_kw):
        cls = ContractClass.AVOID_HARM
    elif any(k in text for k in confirm_kw):
        cls = ContractClass.CONFIRM
    elif any(k in text for k in explore_kw):
        cls = ContractClass.EXPLORE

    # Find the right template suggestions.
    presets = suggest_contracts_for_template(template_id) if template_id else []
    if not presets:
        # No template → fallback to research (most generic).
        presets = suggest_contracts_for_template("research")
    chosen = next((c for c in presets if c.contract_class == cls), None)
    if chosen is None and presets:
        chosen = presets[0]
    if chosen is None:
        return jsonify({"ok": False, "error": "no contracts available"}), 200
    out = chosen.to_dict()
    out["source"] = "inferred"
    return jsonify({"ok": True, "contract": out, "source": "inferred"})


# ---------------------------------------------------------------------------
# Runs list
# ---------------------------------------------------------------------------

@app.route("/api/runs")
def api_runs():
    if not _HAVE_STATE:
        return jsonify({"ok": False, "runs": [],
                        "reason": _STATE_IMPORT_ERROR}), 200
    return jsonify({"ok": True, "runs": list_runs(DEFAULT_OUTPUTS)})


# ---------------------------------------------------------------------------
# Demo datasets — listed for the empty-state chips
# ---------------------------------------------------------------------------

def _resolve_demo_dir() -> Path:
    """Find ``data/fixtures/`` — prefer worktree, fall back to runner repo."""
    candidates = [
        REPO_ROOT / "data" / "fixtures",
        _detect_runner_repo() / "data" / "fixtures",
    ]
    for c in candidates:
        if c.exists() and any(c.glob("*.csv")):
            return c
    return candidates[0]


@app.route("/api/demo_datasets")
def api_demo_datasets():
    """List the bundled demo datasets with metadata for the empty-state chips."""
    demo_dir = _resolve_demo_dir()
    # Manifest mirrors tools/gen_demo_data.py:DEMO_MANIFEST. Kept here for the
    # API since the tools/ module isn't on sys.path at runtime.
    manifest = [
        {
            "id": "climate_buoy",
            "filename": "climate_buoy_demo.csv",
            "title": "Climate buoy",
            "subtitle": "365-day temp / precip / humidity",
            "domain": "enviro",
            "highlights": ["regime shift mid-year",
                           "5 precipitation anomalies",
                           "Newton's-cooling-fittable target"],
        },
        {
            "id": "factory_bearing",
            "filename": "factory_bearing_demo.csv",
            "title": "Factory bearing",
            "subtitle": "1000-sample vibration sensor at 10 Hz",
            "domain": "industrial",
            "highlights": ["damped harmonic oscillator signal",
                           "3 impulse anomalies",
                           "degradation regime in last quarter"],
        },
        {
            "id": "patient_cohort",
            "filename": "patient_cohort_demo.csv",
            "title": "Patient cohort",
            "subtitle": "200 patients, cross-sectional, mixed types",
            "domain": "research",
            "highlights": ["no time axis (honest-mode path)",
                           "treatment-effect causal signal",
                           "BMI / cholesterol / BP correlations"],
        },
    ]
    out = []
    for m in manifest:
        path = demo_dir / m["filename"]
        out.append({
            **m,
            "path": str(path) if path.exists() else None,
            "available": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else 0,
        })
    return jsonify({"ok": True, "demo_dir": str(demo_dir), "datasets": out})


# ---------------------------------------------------------------------------
# Trigger a run
# ---------------------------------------------------------------------------

def _extract_last_json(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract the last JSON object from runner stdout.

    The runner emits ``print(json.dumps(out, indent=2))`` — multi-line indented
    JSON, possibly preceded by progress logs. Strategy:
      1. Try to parse the entire stdout.
      2. Walk lines from bottom up, find the outermost ``{`` that starts a
         complete JSON object.
    """
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("{"):
            candidate = "\n".join(lines[i:])
            try:
                return json.loads(candidate)
            except Exception:
                continue
    return None


def _detect_runner_repo() -> Path:
    """Find the directory containing scripts/run_aurora_with_steps_1_4.py.

    Prefer the **main repo** when running from a worktree, because:
      * the user's existing ``outputs/aurora_dataset_runs`` history lives there
      * the runner uses ``Path(__file__).resolve().parents[1]`` to compute its
        own outputs root, so we want ``__file__`` to land in the main repo
      * datasets registered via ``fantasyai/data/external/...`` resolve there

    Resolution order:
      1. ``AURORA_RUNNER_REPO`` env var (manual override)
      2. nearest ancestor with both ``scripts/run_aurora_with_steps_1_4.py``
         AND a populated ``outputs/aurora_dataset_runs/``
      3. nearest ancestor with the runner script (regardless of outputs)
      4. REPO_ROOT itself
    """
    env_override = os.environ.get("AURORA_RUNNER_REPO")
    if env_override and (Path(env_override) / "scripts" / "run_aurora_with_steps_1_4.py").exists():
        return Path(env_override)

    def has_runner(p: Path) -> bool:
        return (p / "scripts" / "run_aurora_with_steps_1_4.py").exists()

    def has_outputs(p: Path) -> bool:
        out = p / "outputs" / "aurora_dataset_runs"
        try:
            return out.exists() and any(out.iterdir())
        except Exception:
            return False

    # Walk ancestors first looking for the populated parent (main repo).
    candidates: list[Path] = []
    p = REPO_ROOT
    for _ in range(6):
        candidates.append(p)
        if p == p.parent:
            break
        p = p.parent

    # Pass 1: prefer ancestors that have the runner AND a populated outputs dir.
    for c in candidates:
        if has_runner(c) and has_outputs(c):
            return c
    # Pass 2: any ancestor with the runner.
    for c in candidates:
        if has_runner(c):
            return c
    return REPO_ROOT


def _last_stderr_line(stderr: str) -> str:
    """Pull the last meaningful line from a stderr blob — usually the punchline."""
    lines = [ln for ln in (stderr or "").splitlines()
             if ln.strip() and not ln.startswith(("Traceback ", "  File "))]
    if not lines:
        # If only the traceback lines exist, take the last one anyway.
        all_lines = [ln for ln in (stderr or "").splitlines() if ln.strip()]
        return all_lines[-1][:400] if all_lines else ""
    return lines[-1][:400]


def _trigger_tiered_run(*, dataset: str, seed: int,
                         also_math: bool, math_v2: bool, deep_math: bool,
                         also_llm: bool, also_motif: bool, also_orchestrator: bool,
                         flags_tuple,
                         run_id: Optional[str] = None) -> Dict[str, Any]:
    """Tiered orchestrator path. Returns when T1 completes; T2 runs in background.

    The parent run_dir holds _SAMPLING_STATE.json + _TIER_RUNS.json. Each
    tier's actual analysis artifacts live in a *child* run_dir produced by
    the standard runner subprocess. ``state_builder`` reads the parent and
    transparently composes a unified state with replication_status attached.
    """
    try:
        from fantasyai.aurora.sampling import start_tiered_run
        from fantasyai.aurora.sampling.state_machine import SAMPLING_STATE_FILE
    except Exception as e:
        return {"ok": False, "run_dir": None, "tiered": True,
                "error_summary": f"sampling module unavailable: {e}",
                "stdout": "", "stderr": "", "returncode": -1,
                "cmd": [], "cwd": "", "elapsed_seconds": 0.0}

    ds_path = Path(dataset).resolve()
    ts = time.strftime("%Y%m%d_%H%M%S")
    parent_run_dir = (
        Path(DEFAULT_OUTPUTS) / f"{ts}__tiered__{ds_path.name}"
    )
    parent_run_dir.mkdir(parents=True, exist_ok=True)

    # TIER FIX: register the parent run_dir on the run-registry IMMEDIATELY
    # so the tier-observer thread can start tailing _SAMPLING_STATE.json
    # while T0/T1 are still running. Previously the observer only saw the
    # run_dir AFTER _trigger_run returned (i.e. after T1 completed), which
    # meant a 1GB dataset would sit at "RUNNING" with no tier info for
    # several minutes — silently violating the 30-second-update SLA.
    if run_id is not None:
        try:
            import fantasyai.aurora.run_registry as _reg
            _reg.set_run_dir(run_id, str(parent_run_dir))
        except Exception:
            pass

    # Per-tier callback: invoke the standard runner subprocess on a sample.
    # T1 runs LEAN (no motif/orchestrator/LLM) so it stays inside the 60s
    # budget. T2 turns those back on. T3 is full-fat. Each tier gets its
    # own subprocess timeout so a slow runner fails its own tier loudly
    # instead of stalling the user.
    #
    # Why state-keeping here: closures over current_tier let us detect
    # which tier the orchestrator is currently calling us from.
    current_tier = {"n": 1}
    # Honest budgets: the current runner subprocess does 7+ analytical stages
    # (deep_math + physics + forecasting + regimes + symbolic discovery + …)
    # plus Python startup × 2 (delegate runner pattern). Even on a 100K-row
    # sample with motif/orchestrator/LLM disabled, this typically takes
    # 90–180s. The spec target of <60s requires a native T1 (no subprocess);
    # see verify_part1.py output. Until that ships, we use realistic timeouts.
    TIER_TIMEOUTS = {1: 240, 2: 600, 3: 1800}   # 4 min / 10 min / 30 min

    def _runner_for_tier(sample_path: Path,
                          parent_run_dir_arg: Path) -> Optional[Path]:
        n = current_tier["n"]
        # T1: lean — only structure + deep_math. Skip motif clustering,
        # orchestrator planning, LLM narrative — those are the slow ones
        # that don't shrink with the sample size.
        if n == 1:
            tier_flags = dict(also_math=False, math_v2=False,
                               deep_math=True,
                               also_llm=False, also_motif=False,
                               also_orchestrator=False)
            tier_label = "Tier 1 (quick sample)"
        elif n == 2:
            # T2: confirmation pass — turn motif + orchestrator back on,
            # still skip LLM (saves ~30s and we already have T1's narrative).
            tier_flags = dict(also_math=also_math, math_v2=math_v2,
                               deep_math=deep_math, also_llm=False,
                               also_motif=also_motif,
                               also_orchestrator=also_orchestrator)
            tier_label = "Tier 2 (confirmation)"
        else:
            # T3: full-fat with everything the user originally asked for.
            tier_flags = dict(also_math=also_math, math_v2=math_v2,
                               deep_math=deep_math, also_llm=also_llm,
                               also_motif=also_motif,
                               also_orchestrator=also_orchestrator)
            tier_label = "Tier 3 (full data)"

        result = _trigger_run(
            dataset=str(sample_path), seed=seed,
            **tier_flags,
            use_cache=False,
            timeout_seconds=TIER_TIMEOUTS.get(n, 600),
            tier_label=tier_label,
        )
        if result.get("ok") and result.get("run_dir"):
            return Path(result["run_dir"])
        return None

    # Small-dataset short-circuit: skip tiering entirely. Use full flags
    # (the user wants the complete pipeline on tiny data, not lean T1).
    def _runner_for_small(file_path_arg: Path,
                           parent_run_dir_arg: Path) -> Optional[Path]:
        result = _trigger_run(
            dataset=str(file_path_arg), seed=seed,
            also_math=also_math, math_v2=math_v2, deep_math=deep_math,
            also_llm=also_llm, also_motif=also_motif,
            also_orchestrator=also_orchestrator,
            use_cache=False,
            timeout_seconds=600,
            tier_label="Single-shot (small data)",
        )
        if result.get("ok") and result.get("run_dir"):
            return Path(result["run_dir"])
        return None

    # Stamp the parent's cache key BEFORE the run so re-running the same file
    # finds the parent and replays it.
    if _HAVE_CACHE:
        try:
            ck = compute_cache_key(ds_path, seed, flags_tuple)
            write_cache_key(parent_run_dir, ck)
        except Exception:
            pass

    def _on_tier(n, _state):
        # Bump the closure pointer so the next tier picks the right flags.
        # Orchestrator fires on_tier_complete after each tier finishes.
        current_tier["n"] = n + 1

    start = time.time()
    try:
        state = start_tiered_run(
            file_path=ds_path,
            parent_run_dir=parent_run_dir,
            runner=_runner_for_tier,
            small_runner=_runner_for_small,
            background_t2=True,
            on_tier_complete=_on_tier,
        )
    except Exception as e:
        return {"ok": False, "run_dir": str(parent_run_dir), "tiered": True,
                "error_summary": f"tiered orchestrator failed: {e}",
                "returncode": -1, "elapsed_seconds": time.time() - start,
                "stdout": "", "stderr": str(e),
                "cmd": ["tiered"], "cwd": str(parent_run_dir)}

    # Touch parent's mtime so /api/state's "latest run" lookup picks it.
    try: os.utime(parent_run_dir, None)
    except Exception: pass

    highest = state.highest_complete_tier()
    ok = highest >= 1 or state.is_small_dataset
    return {
        "ok": ok,
        "run_dir": str(parent_run_dir),
        "tiered": True,
        "tier_state": state.to_dict(),
        "highest_tier": highest,
        "cached": False,
        "returncode": 0 if ok else -1,
        "error_summary": None if ok else
            (state.tiers["1"].error or "tier 1 did not complete"),
        "elapsed_seconds": round(time.time() - start, 2),
        "stdout": "", "stderr": "",
        "cmd": ["tiered"], "cwd": str(parent_run_dir),
    }


def _trigger_run(dataset: str, seed: int = 42, also_math: bool = True,
                 math_v2: bool = True, deep_math: bool = True,
                 also_llm: bool = True, also_motif: bool = True,
                 also_orchestrator: bool = True,
                 use_cache: bool = True,
                 timeout_seconds: int = 600,
                 tier_label: Optional[str] = None,
                 run_id: Optional[str] = None) -> Dict[str, Any]:
    """Shared run trigger used by both /api/run and /api/upload.

    Two paths:
    * **Small dataset (default)** — single-shot subprocess; all the existing
      machinery applies (cache, validation, error reporting).
    * **Large dataset** — routes through the tiered orchestrator (T0→T1→T2)
      so the user gets findings within ~60 seconds even on huge files.

    Both paths share cache lookup. Cache hits are honoured regardless of size.
    """
    flags_tuple = ()
    if _HAVE_CACHE:
        flags_tuple = normalize_flags(
            also_math=also_math, math_v2=math_v2, deep_math=deep_math,
            also_llm=also_llm, also_motif=also_motif,
            also_orchestrator=also_orchestrator,
        )

    # ---- Cache lookup ------------------------------------------------------
    if use_cache and _HAVE_CACHE:
        try:
            ck = compute_cache_key(Path(dataset), seed, flags_tuple)
            cached = find_cached_run(DEFAULT_OUTPUTS, ck)
            if cached is not None:
                # Touch the cached dir's mtime so /api/state's "latest" lookup
                # picks IT — not whatever happened to be most-recently run.
                # Without this, the frontend's refreshState() displays a
                # different run than the one /api/run resolved to. Critical
                # for fast cache-hit UX.
                try:
                    os.utime(cached, None)
                except Exception:
                    pass
                return {
                    "ok": True,
                    "run_dir": str(cached),
                    "returncode": 0,
                    "error_summary": None,
                    "stdout": "", "stderr": "",
                    "cmd": [], "cwd": "",
                    "elapsed_seconds": 0.0,
                    "cached": True,
                    "cache_key": ck.compute_hash(),
                    "input_hash": ck.input_hash,
                }
        except Exception:
            pass  # cache lookup errors should never block a real run

    # ---- Tiered analysis branch for large datasets -------------------------
    # For files above the size threshold, route through the orchestrator so
    # the user sees findings within ~60s even on 100M-row files.
    try:
        ds_path = Path(dataset)
        ds_size = ds_path.stat().st_size if ds_path.exists() else 0
    except Exception:
        ds_size = 0
    try:
        from fantasyai.aurora.sampling import (
            SMALL_DATASET_BYTE_THRESHOLD as _SAMPLING_BYTES,
        )
    except Exception:
        _SAMPLING_BYTES = 50 * 1024 * 1024
    if ds_size > _SAMPLING_BYTES and ds_path.exists() and ds_path.suffix.lower() == ".csv":
        return _trigger_tiered_run(
            dataset=dataset, seed=seed,
            also_math=also_math, math_v2=math_v2, deep_math=deep_math,
            also_llm=also_llm, also_motif=also_motif,
            also_orchestrator=also_orchestrator,
            flags_tuple=flags_tuple,
            run_id=run_id,
        )

    cmd = [PYTHON, "-m", RUNNER_MODULE, "--dataset", dataset, "--seed", str(seed)]
    if also_math:         cmd.append("--also-math")
    if math_v2:           cmd.append("--math-v2")
    if deep_math:         cmd.append("--deep-math")
    if also_llm:          cmd.append("--also-llm")
    # Workstream-3A hotfix: ``--also-motif`` and ``--also-orchestrator``
    # are stale flag names from a previous runner version. The motif
    # discovery + orchestrator planning stages are now UNCONDITIONAL
    # inside the runner, so the flags map to no-ops. They are not in
    # ``run_aurora_with_steps_1_4.py``'s argparse list (only --also-math,
    # --math-v2, --deep-math, --also-llm are), so passing them caused
    # ``error: unrecognized arguments: --also-motif --also-orchestrator``
    # and the run died at argparse before any analysis started.
    #
    # The kwargs are KEPT on the function signature (and on
    # ``cache.normalize_flags``) so existing cache keys remain stable
    # — the on-disk cache was keyed with these flags and we don't
    # want to invalidate it. Only the cmd-line emission is removed.

    repo_for_runner = _detect_runner_repo()

    # CRITICAL: timestamp before subprocess so we can verify a NEW run dir appeared.
    start_ts = time.time()

    try:
        proc = subprocess.run(
            cmd, cwd=str(repo_for_runner),
            capture_output=True, text=True, timeout=int(timeout_seconds),
        )
    except subprocess.TimeoutExpired as e:
        # Tier-aware messaging: replace the generic "10 minutes" string with
        # the actual budget that was exceeded for this stage.
        mins = max(1, int(timeout_seconds) // 60)
        secs = int(timeout_seconds) % 60
        budget_text = (f"{mins} min" if secs == 0
                       else f"{mins} min {secs}s" if mins > 0
                       else f"{secs}s")
        prefix = f"{tier_label}: " if tier_label else ""
        return {"ok": False, "error": f"timeout (>{budget_text})",
                "error_summary": f"{prefix}Runner exceeded the {budget_text} budget.",
                "tier_label": tier_label,
                "stdout": (e.stdout or "")[-8000:],
                "stderr": (e.stderr or "")[-8000:],
                "returncode": -1, "run_dir": None,
                "cmd": cmd, "cwd": str(repo_for_runner),
                "elapsed_seconds": time.time() - start_ts}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"runner not invokable: {e}",
                "error_summary": f"Cannot launch runner: {e}",
                "stdout": "", "stderr": str(e),
                "returncode": -1, "run_dir": None,
                "cmd": cmd, "cwd": str(repo_for_runner),
                "elapsed_seconds": time.time() - start_ts}

    elapsed = time.time() - start_ts
    parsed = _extract_last_json(proc.stdout or "")

    # 1. Did subprocess crash?
    if proc.returncode != 0:
        err = _last_stderr_line(proc.stderr or "") or "subprocess returned nonzero"
        if parsed and isinstance(parsed.get("error"), str):
            err = parsed["error"][:400]
        return {
            "ok": False, "run_dir": None, "returncode": proc.returncode,
            "error_summary": err,
            "stdout": (proc.stdout or "")[-8000:],
            "stderr": (proc.stderr or "")[-8000:],
            "cmd": cmd, "cwd": str(repo_for_runner),
            "elapsed_seconds": elapsed,
            "parsed": parsed,
        }

    # 2. Did the runner self-report failure?
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        err = parsed.get("error") or "runner self-reported failure (out['ok']=false)"
        return {
            "ok": False, "run_dir": parsed.get("run_dir"),
            "returncode": proc.returncode,
            "error_summary": str(err)[:400],
            "stdout": (proc.stdout or "")[-8000:],
            "stderr": (proc.stderr or "")[-8000:],
            "cmd": cmd, "cwd": str(repo_for_runner),
            "elapsed_seconds": elapsed,
            "parsed": parsed,
        }

    # 3. Did a NEW run directory actually appear?
    run_dir = (parsed or {}).get("run_dir") if isinstance(parsed, dict) else None
    new_dir_seen = False
    if run_dir and Path(run_dir).exists() and Path(run_dir).stat().st_mtime >= start_ts - 1:
        new_dir_seen = True
    elif _HAVE_STATE:
        # Scan outputs root for any dir created since start_ts.
        try:
            for p in Path(DEFAULT_OUTPUTS).iterdir():
                if p.is_dir() and p.stat().st_mtime >= start_ts - 1:
                    run_dir = str(p)
                    new_dir_seen = True
                    break
        except Exception:
            pass

    if not new_dir_seen:
        return {
            "ok": False, "run_dir": None, "returncode": proc.returncode,
            "error_summary": ("Runner exited cleanly but produced no new output directory. "
                              "Check stderr for hidden errors."),
            "stdout": (proc.stdout or "")[-8000:],
            "stderr": (proc.stderr or "")[-8000:],
            "cmd": cmd, "cwd": str(repo_for_runner),
            "elapsed_seconds": elapsed,
            "parsed": parsed,
        }

    # Stamp the run dir with its cache key so future identical (data+seed+flags)
    # runs can replay instantly. Best-effort — never blocks the response.
    if _HAVE_CACHE and run_dir:
        try:
            ck = compute_cache_key(Path(dataset), seed, flags_tuple)
            write_cache_key(Path(run_dir), ck)
        except Exception:
            pass

    return {
        "ok": True,
        "run_dir": run_dir,
        "returncode": proc.returncode,
        "error_summary": None,
        "stdout": (proc.stdout or "")[-8000:],
        "stderr": (proc.stderr or "")[-8000:],
        "cmd": cmd,
        "cwd": str(repo_for_runner),
        "elapsed_seconds": elapsed,
        "cached": False,
    }


@app.route("/api/run/preflight", methods=["POST"])
def api_run_preflight():
    """Quick-check what RUN ANALYSIS will actually do — no subprocess.

    Body: {dataset}
    Returns: {ok, resolves_to: <abs_path>, exists, rows_estimate, cols, error?}

    Used by the frontend to show the user exactly which file will run
    when they click ▶ RUN ANALYSIS.
    """
    body = request.get_json(silent=True) or {}
    ds = body.get("dataset") or "env_air_quality"
    out: Dict[str, Any] = {
        "ok": True, "dataset": ds,
        "resolves_to": None, "exists": False,
        "rows_estimate": None, "cols": None,
    }
    # If the value looks like a path, resolve it.
    p = Path(ds)
    if p.is_absolute() or p.exists():
        rp = p.resolve()
        out["resolves_to"] = str(rp)
        out["exists"] = rp.exists()
        if rp.exists() and rp.suffix.lower() == ".csv":
            try:
                import pandas as pd
                head = pd.read_csv(rp, nrows=5)
                out["cols"] = int(head.shape[1])
                with open(rp, "rb") as fh:
                    out["rows_estimate"] = sum(1 for _ in fh) - 1
            except Exception as e:
                out["error"] = f"could not preview CSV: {e}"
    else:
        # Symbolic name — let the user know the runner will resolve it.
        out["resolves_to"] = f"<runner-resolved name: {ds}>"
        out["exists"] = True
    return jsonify(out)


# ---------------------------------------------------------------------------
# F.1: dataset profile (Tier 0 only) — the new bridge between dataset
# acquisition and analysis kickoff.
# ---------------------------------------------------------------------------

# Empirical rows-per-second baseline by detected shape. Used to estimate
# runtime for the tier selector. These are honest order-of-magnitude
# estimates; the UI labels them as "~ Xs" / "~ Xm" so users don't expect
# precision.
_RUNTIME_BASELINE_ROWS_PER_SEC = {
    "small":       50_000,
    "time_series": 25_000,
    "panel":       15_000,
    "wide":        20_000,
    "very_wide":    8_000,
    "tall_sparse":  6_000,
    "network":     30_000,
}


def _estimate_runtimes(rows: int, cols: int, shape: str) -> Dict[str, str]:
    """Compute estimated wall-clock runtimes for the four tier presets.
    Returns human-friendly strings ("~ 12 seconds", "~ 2 minutes")."""
    base = _RUNTIME_BASELINE_ROWS_PER_SEC.get(shape, 20_000)
    col_penalty = 1.0 / max(1.0, cols / 50.0)
    effective_rps = base * col_penalty
    full_seconds = max(5.0, rows / max(1.0, effective_rps))
    quick_seconds    = min(60.0, full_seconds * 0.05 + 8.0)
    standard_seconds = min(180.0, full_seconds * 0.20 + 30.0)

    def _human(s: float) -> str:
        if s < 60:
            return f"~ {int(round(s))} seconds"
        if s < 3600:
            return f"~ {int(round(s / 60))} minutes"
        if s < 7200:
            return "~ 1 hour"
        return "~ 1 hour+"

    return {
        "quick":    _human(quick_seconds),
        "standard": _human(standard_seconds),
        "full":     _human(full_seconds),
    }


def _auto_recommendation(rows: int) -> str:
    """Per the brief: <100K → full; 100K-10M → standard; >10M → quick."""
    if rows < 100_000:
        return "full"
    if rows < 10_000_000:
        return "standard"
    return "quick"


def _profile_template_guess(profile_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort template scoring against the StructureProfile alone.
    Returns {template_id, confidence}. Falls back gracefully when the
    system_model module is unavailable."""
    try:
        from fantasyai.aurora.system_model import best_template_for
        cols = [c.get("name", "") for c in (profile_dict.get("columns") or [])]
        cadence = None
        if profile_dict.get("cadence_seconds"):
            s = float(profile_dict["cadence_seconds"])
            cadence = ("subhourly" if s < 3600 else
                        "hourly" if s < 86400 else "daily")
        best, score = best_template_for(cols, cadence=cadence)
        return {
            "template_id": getattr(best, "id", None),
            "template_confidence": float(score) if score is not None else None,
        }
    except Exception:
        return {"template_id": None, "template_confidence": None}


@app.route("/api/dataset/profile", methods=["POST"])
def api_dataset_profile():
    """F.1: structure inference + runtime estimates. ZERO analysis.

    Body::

        {"path": "/abs/path/to/data.csv"}

    Response::

        {
          "ok": true,
          "path": "/abs/path/to/data.csv",
          "size_bytes": 134217728,
          "size_mb": 128.0,
          "tier0_profile": {                 // StructureProfile.to_dict()
            "rows_total": 2075259,
            "cols_total": 12,
            "shape": "time_series",
            "time_col": "timestamp",
            "panel_id_col": null,
            "is_small": false,
            "elapsed_s": 6.4,
            "columns": [...]
          },
          "estimated_runtime": {
            "quick":    "~ 12 seconds",
            "standard": "~ 2 minutes",
            "full":     "~ 45 minutes"
          },
          "auto_recommendation": "standard",
          "template_id": "enviro",
          "template_confidence": 0.78,
          "warnings": []
        }

    The endpoint is idempotent + cheap; same file → same answer (modulo
    elapsed_s). It's safe for the frontend to call on every dataset
    selection event without throttling.
    """
    body = request.get_json(silent=True) or {}
    raw_path = body.get("path") or body.get("dataset") or ""
    if not raw_path:
        return jsonify({"ok": False, "error": "path required"}), 400

    p = Path(raw_path)
    if not p.is_absolute():
        # Allow demo / relative paths to resolve under common roots.
        for candidate in (Path.cwd() / raw_path,
                            REPO_ROOT / raw_path,
                            Path("data/fixtures") / raw_path):
            if candidate.exists():
                p = candidate.resolve()
                break
    if not p.exists():
        return jsonify({"ok": False, "error": f"file not found: {raw_path}"}), 404
    if not p.is_file():
        return jsonify({"ok": False, "error": f"not a file: {raw_path}"}), 400

    size_bytes = p.stat().st_size
    warnings: list = []

    # Run Tier 0 only — no analysis kicks off here.
    try:
        from fantasyai.aurora.sampling import infer_structure
        profile = infer_structure(p)
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"structure inference failed: {type(e).__name__}: {e}",
            "path": str(p),
        }), 500

    profile_dict = profile.to_dict()
    rows = int(profile_dict.get("rows_total") or 0)
    cols = int(profile_dict.get("cols_total") or 0)
    shape = profile_dict.get("shape") or "wide"

    # Honesty: if the file is huge enough that even Tier 0 took a while,
    # surface that to the user so they understand the wait.
    if profile_dict.get("elapsed_s", 0) > 30:
        warnings.append(
            f"Tier 0 (structure inference) took "
            f"{profile_dict['elapsed_s']:.1f}s on this file."
        )

    estimates = _estimate_runtimes(rows, cols, shape)
    auto = _auto_recommendation(rows)
    tmpl = _profile_template_guess(profile_dict)

    return jsonify({
        "ok": True,
        "path": str(p),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "tier0_profile": profile_dict,
        "estimated_runtime": estimates,
        "auto_recommendation": auto,
        "template_id": tmpl["template_id"],
        "template_confidence": tmpl["template_confidence"],
        "warnings": warnings,
    })


@app.route("/api/run", methods=["POST"])
def api_run():
    """Trigger a fresh analysis. ASYNC kickoff (Problem 2 of the hardening
    sprint).

    Body (all optional)::

        {
          "dataset": "env_air_quality" | "/abs/or/repo/relative/path.csv",
          "seed": 42,
          "also_math": true,
          "math_v2": true,
          "deep_math": true,
          "also_llm": true,
          "sync": false                  # set true to keep the legacy blocking shape
        }

    Response (when async, the default)::

        {
          "ok": true,
          "async": true,
          "run_id": "<id>",
          "run_dir": null,                # populated once the orchestrator picks one
          "status_url": "/api/run/status?run_id=<id>",
          "dataset": "<echoed>",
          "started_at": "<iso>"
        }

    Cache hits return synchronously with ``async: false`` so demo flows stay
    instant. Failures during kickoff (e.g. the dataset doesn't exist) ALSO
    return synchronously with ``ok: false`` so the frontend can surface the
    error immediately instead of polling for nothing.
    """
    body = request.get_json(force=True, silent=True) or {}
    dataset = body.get("dataset", "env_air_quality")
    seed = int(body.get("seed", 42))
    flags = {
        "also_math": bool(body.get("also_math", True)),
        "math_v2": bool(body.get("math_v2", True)),
        "deep_math": bool(body.get("deep_math", True)),
        "also_llm": bool(body.get("also_llm", True)),
        "also_motif": bool(body.get("also_motif", True)),
        "also_orchestrator": bool(body.get("also_orchestrator", True)),
    }
    sync = bool(body.get("sync", False))
    # Atom 3.2: optional seed text — what the user is trying to figure out.
    # Affects ordering of findings via SeedRanker; never excludes from analysis.
    seed_text = body.get("seed_text")
    if seed_text is not None:
        seed_text = str(seed_text).strip() or None

    # ---- Synchronous path: cache hits + caller asked for sync ---------
    # Cache hits resolve in <50ms — no point spinning a thread.
    if sync:
        result = _trigger_run(dataset=dataset, seed=seed, **flags)
        result["dataset"] = dataset
        result["async"] = False
        return jsonify(result)

    # ---- Async kickoff -----------------------------------------------
    try:
        from fantasyai.aurora import run_registry as _reg
    except Exception as e:
        # Registry import failure → fall back to legacy synchronous behaviour
        # so a misconfigured deploy still works.
        result = _trigger_run(dataset=dataset, seed=seed, **flags)
        result["dataset"] = dataset
        result["async"] = False
        result["registry_error"] = f"{type(e).__name__}: {e}"
        return jsonify(result)

    # If the cache will hit, do it synchronously — fast path.
    if _HAVE_CACHE:
        try:
            flags_tuple = normalize_flags(**flags)
            ck = compute_cache_key(Path(dataset), seed, flags_tuple)
            cached = find_cached_run(DEFAULT_OUTPUTS, ck)
            if cached is not None:
                run_id = _reg.make_run_id(dataset)
                _reg.register(run_id, str(dataset), seed=seed, flags=flags,
                                seed_text=seed_text)
                _reg.set_run_dir(run_id, str(cached))
                _reg.complete(run_id, run_dir=str(cached), cached=True)
                try:
                    os.utime(cached, None)
                except Exception:
                    pass
                return jsonify({
                    "ok": True,
                    "async": False,
                    "cached": True,
                    "run_id": run_id,
                    "run_dir": str(cached),
                    "dataset": dataset,
                    "started_at": _reg.snapshot(run_id).get("started_at"),
                    "completed_at": _reg.snapshot(run_id).get("completed_at"),
                })
        except Exception:
            pass  # cache failures fall through to async kickoff

    # Pre-flight: if the dataset doesn't exist, fail synchronously NOW so
    # the user sees a real error instead of polling status forever.
    ds_path = Path(dataset)
    if ds_path.is_absolute() or "/" in dataset or "\\" in dataset:
        # Treat as a path; verify existence.
        if not ds_path.exists():
            return jsonify({
                "ok": False,
                "async": False,
                "error": f"dataset not found: {dataset}",
                "dataset": dataset,
            }), 200    # 200 + ok:false so the frontend's error UI fires

    run_id = _reg.make_run_id(dataset)
    _reg.register(run_id, str(dataset), seed=seed, flags=flags,
                    seed_text=seed_text)
    _reg.set_state(run_id, _reg.RunState.RUNNING,
                    message="kicking off subprocess")

    # Daemon thread runs the existing _trigger_run synchronously, then
    # records terminal state on the registry. Captures dataset args by closure.
    # While the runner is in flight we also tail _SAMPLING_STATE.json from the
    # parent run_dir (when one appears) and translate per-tier transitions into
    # registry state. The runner is unchanged; the registry simply observes
    # the on-disk state machine the orchestrator already maintains.
    def _runner_thread():
        try:
            result = _trigger_run(dataset=dataset, seed=seed, run_id=run_id, **flags)
            run_dir = result.get("run_dir")
            cached = bool(result.get("cached"))
            ok = bool(result.get("ok"))
            err = result.get("error_summary") or result.get("error")
            if run_dir:
                _reg.set_run_dir(run_id, run_dir)
                # Final tier-state sync from disk so the snapshot is accurate.
                try:
                    from fantasyai.aurora.sampling import SamplingState
                    ss = SamplingState.load(Path(run_dir))
                    if ss is not None:
                        ht = ss.highest_complete_tier()
                        if ht >= 1:
                            _reg.set_state(
                                run_id, _reg.RunState.TIER1_COMPLETE
                                if ht == 1 else (
                                    _reg.RunState.TIER2_COMPLETE if ht == 2
                                    else _reg.RunState.COMPLETE
                                ),
                                current_tier=ht, tiered=True,
                                progress_pct=100.0 if ht >= 3 else
                                              (66.0 if ht == 2 else 33.0),
                            )
                except Exception:
                    pass
            if ok:
                _reg.complete(run_id, run_dir=run_dir, cached=cached)
            else:
                _reg.fail(run_id, err or "unknown error")
        except Exception as e:
            _reg.fail(run_id, f"{type(e).__name__}: {e}")

    def _tier_observer_thread():
        """Tail the parent run_dir's _SAMPLING_STATE.json and translate
        transitions into registry state. Stops when the run is terminal.

        The orchestrator atomically writes that file at every tier transition.
        We poll lazily (1 Hz) so it stays cheap even for fast runs.
        """
        last_state = None
        last_tier = None
        for _ in range(7200):    # 2 hours hard cap
            time.sleep(1.0)
            snap = _reg.snapshot(run_id) or {}
            if snap.get("state") in {_reg.RunState.COMPLETE,
                                       _reg.RunState.FAILED,
                                       _reg.RunState.CANCELLED}:
                return
            rd = snap.get("run_dir")
            if not rd:
                continue
            try:
                from fantasyai.aurora.sampling import SamplingState
                ss = SamplingState.load(Path(rd))
                if ss is None:
                    # Non-tiered run; mark RUNNING and let _runner_thread close.
                    if snap.get("state") == _reg.RunState.PENDING:
                        _reg.set_state(run_id, _reg.RunState.RUNNING,
                                        message="analyzing", progress_pct=10.0)
                    continue
                d = ss.to_dict()
                tiers = d.get("tiers") or {}
                # Find the highest tier whose state is "running" or "complete".
                cur_tier = 0
                cur_state = None
                pct = 0.0
                for n in ("0", "1", "2", "3"):
                    ts = (tiers.get(n) or {}).get("state")
                    p = (tiers.get(n) or {}).get("progress_pct")
                    if ts in ("running", "complete"):
                        cur_tier = int(n)
                        cur_state = ts
                        pct = float(p) if p is not None else pct
                if cur_state == "running":
                    target = {
                        0: _reg.RunState.TIER0_RUNNING,
                        1: _reg.RunState.TIER1_RUNNING,
                        2: _reg.RunState.TIER2_RUNNING,
                        3: _reg.RunState.TIER3_RUNNING,
                    }.get(cur_tier, _reg.RunState.RUNNING)
                elif cur_state == "complete":
                    target = {
                        0: _reg.RunState.TIER0_RUNNING,    # T0 done → T1 next
                        1: _reg.RunState.TIER1_COMPLETE,
                        2: _reg.RunState.TIER2_COMPLETE,
                        3: _reg.RunState.COMPLETE,
                    }.get(cur_tier, _reg.RunState.RUNNING)
                    # Roll a coarse progress from tier index.
                    pct = max(pct, {1: 33.0, 2: 66.0, 3: 100.0}.get(cur_tier, 10.0))
                else:
                    target = _reg.RunState.RUNNING
                if (target, cur_tier) != (last_state, last_tier):
                    _reg.set_state(run_id, target, current_tier=cur_tier,
                                    progress_pct=pct, tiered=True,
                                    message=f"tier {cur_tier}: {cur_state}")
                    last_state = target
                    last_tier = cur_tier
            except Exception:
                continue

    import threading
    threading.Thread(target=_runner_thread, daemon=True,
                     name=f"aurora-run-{run_id}").start()
    threading.Thread(target=_tier_observer_thread, daemon=True,
                     name=f"aurora-tier-obs-{run_id}").start()

    snap = _reg.snapshot(run_id) or {}
    return jsonify({
        "ok": True,
        "async": True,
        "run_id": run_id,
        "run_dir": snap.get("run_dir"),
        "dataset": dataset,
        "status_url": f"/api/run/status?run_id={run_id}",
        "started_at": snap.get("started_at"),
    })


@app.route("/api/run/seed", methods=["POST"])
def api_run_seed():
    """Atom 3.2: update the seed_text on an existing run WITHOUT re-running.

    Body::

        {
          "run_id": "<id>"  OR  "run_dir": "<path>",
          "seed_text": "what the user is now investigating",
          "source": "user" | "copilot" | "user_pivot"
        }

    Effect: updates the run_registry record (and persists _RUN_PROGRESS.json),
    then the next /api/state poll will see the new seed_text in run_meta and
    re-rank findings via the SeedRanker. The underlying analysis is NOT
    re-run; only the surface ordering changes. Empty seed_text clears it.
    """
    try:
        from fantasyai.aurora import run_registry as _reg
    except Exception as e:
        return jsonify({"ok": False, "error": f"registry unavailable: {e}"}), 200
    body = request.get_json(silent=True) or {}
    run_id = body.get("run_id")
    run_dir = body.get("run_dir")
    seed_text = body.get("seed_text")
    source = body.get("source") or "user"

    # Resolve run_id from run_dir if necessary.
    if not run_id and run_dir:
        snap = _reg.lookup_by_run_dir(run_dir)
        if snap:
            run_id = snap.get("run_id")
    if not run_id:
        return jsonify({"ok": False,
                        "error": "run_id or run_dir required"}), 400
    snap = _reg.snapshot(run_id)
    if snap is None:
        return jsonify({"ok": False,
                        "error": f"run_id {run_id} not found"}), 404
    _reg.set_seed(run_id, seed_text, source=source)
    new_snap = _reg.snapshot(run_id) or {}
    return jsonify({
        "ok": True,
        "run_id": run_id,
        "run_dir": new_snap.get("run_dir"),
        "seed_text": new_snap.get("seed_text"),
        "seed_source": new_snap.get("seed_source"),
        "seed_at": new_snap.get("seed_at"),
    })


@app.route("/api/runs/active")
def api_runs_active():
    """Return every non-terminal run currently in the registry.

    Polish-pass / Workstream-1 fix B-4 + F-2: the page-load handler
    calls this BEFORE falling back to find_latest_run. If the response
    contains an active run, the frontend resumes polling for it instead
    of dropping back to a stale completed run from a different day
    (the climate_buoy ghost the maintainer reported).

    Response::

        {"ok": true, "active": [{run_id, run_dir, state, started_at,
                                  progress_pct, current_tier, ...}, ...]}

    Always 200; an empty list means "no run in progress".
    """
    try:
        from fantasyai.aurora import run_registry as _reg
    except Exception as e:
        return jsonify({"ok": False, "error": f"registry unavailable: {e}",
                          "active": []}), 200
    try:
        rows = _reg.list_active()
    except Exception as e:
        return jsonify({"ok": False, "error": f"list_active failed: {e}",
                          "active": []}), 200
    # Sort newest-first so the frontend's auto-resume picks the most
    # recent kickoff when there are somehow multiple.
    rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return jsonify({"ok": True, "active": rows, "count": len(rows)})


@app.route("/api/run/status")
def api_run_status():
    """Return the lifecycle snapshot for a run.

    Query: ``?run_id=<id>`` or ``?run_dir=<path>`` (one is required)

    Response::

        {
          "ok": true,
          "run_id": "...",
          "run_dir": "...",
          "state": "pending|running|tier*_running|tier*_complete|complete|failed|cancelled",
          "progress_pct": 0..100,
          "current_tier": 0|1|2|3|null,
          "message": "human-friendly",
          "started_at": "iso",
          "completed_at": "iso|null",
          "elapsed_seconds": float|null,
          "tiered": bool,
          "error": str|null,
          "cached": bool,
          "transitions": [{"ts","state","tier","pct"}, ...]
        }

    The frontend's polling state machine consumes this every 1.5s while the
    state is non-terminal.
    """
    try:
        from fantasyai.aurora import run_registry as _reg
    except Exception as e:
        return jsonify({"ok": False, "error": f"registry unavailable: {e}"}), 200
    run_id = request.args.get("run_id")
    run_dir = request.args.get("run_dir")
    snap = None
    if run_id:
        snap = _reg.snapshot(run_id)
    elif run_dir:
        snap = _reg.lookup_by_run_dir(run_dir)
    if snap is None:
        return jsonify({
            "ok": False,
            "error": "run not found in registry",
            "run_id": run_id, "run_dir": run_dir,
        }), 200
    # Cross-reference with sampling state when a run_dir exists, so the
    # frontend gets tier-level progress for tiered runs.
    rd = snap.get("run_dir")
    if rd:
        try:
            from fantasyai.aurora.sampling import SamplingState
            ss = SamplingState.load(Path(rd))
            if ss is not None:
                tier_states = ss.to_dict().get("tiers") or {}
                # Promote in-flight tier info if the registry hasn't seen
                # the transition yet (e.g. on a server restart).
                if snap.get("state") in (_reg.RunState.RUNNING,
                                           _reg.RunState.TIER0_RUNNING,
                                           _reg.RunState.TIER1_RUNNING,
                                           _reg.RunState.TIER2_RUNNING,
                                           _reg.RunState.TIER3_RUNNING):
                    for n in ("0", "1", "2", "3"):
                        ts = (tier_states.get(n) or {}).get("state")
                        pct = (tier_states.get(n) or {}).get("progress_pct")
                        if ts == "running":
                            snap["current_tier"] = int(n)
                            if pct is not None:
                                snap["progress_pct"] = float(pct)
                snap["sampling"] = ss.to_dict()
        except Exception:
            pass
    return jsonify({"ok": True, **snap})


# ---------------------------------------------------------------------------
# Upload — accept any supported file format, normalize to CSV, trigger a run
# ---------------------------------------------------------------------------

_SUPPORTED_UPLOAD_EXT = {
    ".csv", ".tsv", ".txt",
    ".json", ".jsonl", ".ndjson",
    ".parquet", ".pq",
    ".xlsx", ".xls",
}

def _safe_dataset_filename(orig: str) -> str:
    """Sanitize an uploaded filename and ensure it has a known extension."""
    base = secure_filename(orig) or "dataset"
    # secure_filename strips dots from intermediate names; ensure single sane ext.
    if "." not in base:
        base += ".csv"
    return base


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Multipart upload: ``file`` field plus optional ``auto_run`` form flag.

    Steps:
      1. Save upload to ``data/uploads/<timestamp>__<safe_name>``.
      2. If non-CSV, load via the multi-format loader and re-write as CSV
         (the runner's --dataset path expects .csv today).
      3. If ``auto_run`` is truthy (default), kick off the runner and return
         the run summary inline so the frontend can refresh.
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file field in form"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "empty filename"}), 400

    safe_name = _safe_dataset_filename(f.filename)
    ext = Path(safe_name).suffix.lower()
    if ext not in _SUPPORTED_UPLOAD_EXT:
        return jsonify({"ok": False, "error": f"unsupported format: {ext}",
                        "supported": sorted(_SUPPORTED_UPLOAD_EXT)}), 400

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    saved_path = UPLOADS_DIR / f"{ts}__{safe_name}"
    f.save(str(saved_path))

    # If not already CSV, normalize through the loader.
    runner_path = saved_path
    notes: list = []
    encoding = None
    rows = cols = 0
    if ext != ".csv":
        if not _HAVE_LOADER:
            return jsonify({"ok": False, "error": "multi-format loader unavailable",
                            "import_error": _LOADER_IMPORT_ERROR}), 500
        try:
            res = load_dataset(saved_path)
            csv_path = saved_path.with_suffix(".csv")
            res.df.to_csv(csv_path, index=False)
            runner_path = csv_path
            notes = list(res.notes or [])
            encoding = res.encoding
            rows = res.rows
            cols = res.cols
        except LoadError as e:
            return jsonify(e.to_dict()), 400
        except Exception as e:
            return jsonify({"ok": False,
                            "error": f"loader failed: {type(e).__name__}: {e}"}), 500
    else:
        # Best-effort row/col probe for the response payload.
        try:
            import pandas as pd
            head = pd.read_csv(saved_path, nrows=10)
            cols = head.shape[1]
            # Approximate rows by counting newlines (cheap; not exact).
            with open(saved_path, "rb") as fh:
                rows = sum(1 for _ in fh) - 1
        except Exception:
            pass

    # F.1: default flipped to '0' — uploads no longer trigger analysis.
    # The frontend now calls /api/dataset/profile after upload, then waits
    # for the user to click RUN ANALYSIS. CLI scripts that rely on the
    # legacy behaviour can still pass auto_run=1 explicitly.
    auto_run = (request.form.get("auto_run") or "0").lower() not in ("0", "false", "")
    seed = int(request.form.get("seed") or 42)

    if not auto_run:
        return jsonify({
            "ok": True,
            "saved": str(runner_path),
            "rows": rows, "cols": cols,
            "encoding": encoding,
            "notes": notes,
            "auto_run": False,
        })

    run_result = _trigger_run(dataset=str(runner_path), seed=seed)
    run_result.update({
        "saved": str(runner_path),
        "rows": rows, "cols": cols,
        "encoding": encoding,
        "notes": notes,
        "auto_run": True,
    })
    return jsonify(run_result)


# ---------------------------------------------------------------------------
# Plot serving
# ---------------------------------------------------------------------------

def _safe_path(p: Path) -> Path:
    """Reject paths outside the repo to mitigate traversal."""
    p = p.resolve()
    if REPO_ROOT not in p.parents and p != REPO_ROOT:
        raise ValueError("Path outside repo root")
    return p


# ---------------------------------------------------------------------------
# Chat — grounded conversation against the current run's artifacts
# ---------------------------------------------------------------------------

def _ollama_available(url: str = "http://127.0.0.1:11434", timeout: float = 1.5) -> bool:
    """Cheap probe — used to decide if we can call Ollama at all."""
    try:
        import requests
        r = requests.get(url.rstrip("/") + "/api/tags", timeout=timeout)
        return r.ok
    except Exception:
        return False


def _build_grounding_brief(state: Dict[str, Any]) -> str:
    """Compact, structured summary of the current run for the LLM system prompt.

    Aim: ~600-1000 tokens. Just the facts the LLM needs to answer questions
    grounded in *this* analysis. We do NOT dump entire deep_math.json — that
    would blow context and let the model hallucinate around irrelevant fields.
    """
    if not state:
        return "No run loaded yet."
    parts = []

    # Synthesis prose first — gives the LLM the human-readable framing
    # before the structured facts. This is the deterministic "what this
    # means" paragraph produced by fantasyai/aurora/synthesis. Putting it
    # at the top lets the copilot adopt the same voice when answering.
    interp = (state.get("interpretive_summary") or "").strip()
    if interp:
        parts.append("=== HEADLINE SYNTHESIS (use this voice) ===")
        parts.append(interp)
        # Domain context notes — short editorial cards from the same engine.
        ctx_notes = state.get("domain_context") or []
        if ctx_notes:
            parts.append("Domain context:")
            for n in ctx_notes[:3]:
                title = (n or {}).get("title") or ""
                note  = (n or {}).get("note")  or ""
                if title and note:
                    parts.append(f"  • {title}: {note}")
        # Equation translations — per-finding intuition from the
        # parameter-range rules. Useful when the user asks "what does
        # zeta=0.31 mean".
        eq_map = state.get("equation_translations") or {}
        if eq_map:
            parts.append("Equation translations available:")
            for fidx, et in list(eq_map.items())[:5]:
                summ = (et or {}).get("summary") or ""
                phrases = ((et or {}).get("intuition_phrases") or [])
                phrase_strs = [
                    f"{p.get('param')}={p.get('value')}: {p.get('phrase')}"
                    for p in phrases if (p or {}).get("phrase")
                ]
                if summ or phrase_strs:
                    line = f"  • finding[{fidx}]: {summ}"
                    if phrase_strs:
                        line += " — " + " | ".join(phrase_strs)
                    parts.append(line)
        parts.append("=== END HEADLINE ===")
        parts.append("")  # blank line before structured facts

    ds = state.get("dataset") or {}
    if ds.get("available"):
        parts.append(f"Dataset: {ds.get('name')} ({ds.get('rows')} rows × {ds.get('cols')} cols).")

    s = state.get("structure") or {}
    if s.get("available"):
        elig = s.get("eligibility") or {}
        time_str = (f"time_col={s.get('time_col')}, cadence={s.get('cadence')}"
                    if s.get('time_axis') else "no valid time axis")
        parts.append(f"Structure: {time_str}. eligible={elig}.")
        cols = s.get("columns") or []
        if cols:
            colnames = ", ".join(c.get("name", "?") for c in cols[:12])
            more = f" (+ {len(cols)-12} more)" if len(cols) > 12 else ""
            parts.append(f"Columns: {colnames}{more}.")

    anoms = state.get("anomalies") or []
    if anoms:
        crit = [a for a in anoms if a.get("severity") == "crit"]
        parts.append(f"Anomalies: {len(anoms)} total, {len(crit)} critical.")
        for a in anoms[:3]:
            parts.append(f"  - {a.get('when')}: {a.get('description')} (severity={a.get('severity')})")

    fc = state.get("forecast") or {}
    if fc.get("available"):
        parts.append(
            f"Forecast: target={fc.get('target')}, method={fc.get('method')}, "
            f"horizon={fc.get('horizon')}, peak={fc.get('headline_value')}, "
            f"phi={fc.get('phi')}, sigma={fc.get('sigma')}."
        )

    cw = state.get("causal") or {}
    if cw.get("available") and cw.get("items"):
        first = cw["items"][0]
        parts.append(f"Causal: {first.get('if_clause')} → {first.get('then_clause')} "
                     f"(method={cw.get('method')}, n={cw.get('n')}).")

    phys = state.get("physics") or {}
    if phys.get("available"):
        law = phys.get("discovered_law") or {}
        if law.get("equation") and law.get("name") != "diagnostic_only":
            parts.append(
                f"Physics: best fit '{law.get('name')}' — {law.get('equation')} "
                f"(RMSE {law.get('rmse')})."
            )
            ru = phys.get("runner_ups") or []
            if ru:
                parts.append("  runner-ups: " + ", ".join(
                    f"{r.get('name')}({r.get('rmse')})" for r in ru[:3]
                ))

    rg = state.get("regimes") or []
    if rg:
        parts.append(f"Regimes: {len(rg)} segments — " +
                     ", ".join(f"{r.get('label')}[{r.get('start')}–{r.get('end')}]"
                               for r in rg[:5]))

    motifs = state.get("motifs") or []
    if motifs:
        parts.append("Motifs: " + ", ".join(m.get("name", "?") for m in motifs[:5]))

    findings = state.get("findings") or []
    if findings:
        parts.append(f"Synthesized findings: {len(findings)}.")
        for f_ in findings[:5]:
            parts.append(f"  - [{f_.get('severity')}] {f_.get('title')}")

    return "\n".join(parts)


# Default model — overridable via OLLAMA_MODEL. Was ``deepseek-r1:8b``; user
# runs ``gemma3:12b`` locally for the studio app, so default to that.
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:12b")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def _ollama_chat(messages: list, model: Optional[str] = None,
                 url: Optional[str] = None, timeout: int = 60) -> Dict[str, Any]:
    """Call Ollama /api/chat. Returns {ok, text, model, error}."""
    import requests
    base = (url or DEFAULT_OLLAMA_URL).rstrip("/")
    mdl = model or DEFAULT_OLLAMA_MODEL
    try:
        r = requests.post(
            f"{base}/api/chat",
            json={"model": mdl, "messages": messages, "stream": False},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        text = ""
        if isinstance(data, dict):
            msg = data.get("message") or {}
            text = msg.get("content") or data.get("response") or ""
        return {"ok": True, "text": text.strip(), "model": mdl, "error": None}
    except Exception as e:
        # Try to surface the actual Ollama error body if available
        # (Ollama 404s with `{"error": "model 'foo' not found, try pulling it..."}`).
        err_body = None
        try:
            err_body = e.response.json() if hasattr(e, "response") and e.response is not None else None
        except Exception:
            pass
        msg = f"{type(e).__name__}: {e}"
        if isinstance(err_body, dict) and err_body.get("error"):
            msg = f"{msg} :: {err_body['error']}"
        return {"ok": False, "text": "", "model": mdl, "error": msg}


def _deterministic_chat_response(message: str, brief: str) -> str:
    """When Ollama isn't reachable, reply with grounded structured info."""
    return (
        "[ungrounded — local LLM not reachable]\n\n"
        f"You asked: {message}\n\n"
        "Here is what I know from the current run:\n"
        f"{brief}\n\n"
        "Start Ollama (http://127.0.0.1:11434) with a chat-capable model "
        "(e.g. `ollama run deepseek-r1:8b`) to get real synthesized answers."
    )


# ---------------------------------------------------------------------------
# Atom 3.4: Copilot 3-layer seed-redirect detection
# ---------------------------------------------------------------------------
# Layer A (explicit affordance) lives in the frontend (#seedInputToggle).
# Layer B (copilot-suggested) — every chat response inspects the recent
#   message + the prior seed and proposes an update with a confidence.
# Layer C (regex safety net) — keyword patterns surface a low-confidence
#   suggestion if Layer B missed an obvious pivot. All three converge on
#   the same suggestion structure; nothing auto-updates without a click.

_PIVOT_REGEX_PATTERNS = [
    r"\b(actually|wait|hold on)[,:]?\s+(?:i\s+)?",
    r"\b(forget that|forget the previous|never mind|nevermind)\b",
    r"\bnow\s+(?:i\s+)?(?:want|care|wonder|need|am interested)\b",
    r"\blet'?s\s+(?:look at|focus on|switch to|talk about)\b",
    r"\b(?:can you )?(?:focus on|switch to|change to)\b",
    r"\bchange (?:my|the) (?:focus|seed|question)\b",
    r"\bshift (?:focus|attention|gears) to\b",
    r"\b(i'?d like to|i want to) (?:look at|see|understand|explore) ([^.?!]+)",
]


def _regex_seed_pivot(user_message: str) -> Optional[Dict[str, Any]]:
    """Layer C: regex safety net. Returns a low-confidence suggestion if
    the user's message looks like a pivot, else None."""
    if not user_message:
        return None
    msg = user_message.strip()
    msg_l = msg.lower()
    matched_pattern = None
    proposed = None
    for pat in _PIVOT_REGEX_PATTERNS:
        m = re.search(pat, msg_l)
        if m:
            matched_pattern = pat
            # Prefer the captured "what" clause if any.
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                proposed = m.group(2).strip().rstrip("?.!,;: ")
            break
    if not matched_pattern:
        return None
    # If we couldn't extract a noun phrase from the pattern, take everything
    # AFTER the pivot phrase as the proposed seed (capped at ~120 chars).
    if not proposed:
        idx = re.search(matched_pattern, msg_l).end()
        tail = msg[idx:].strip().rstrip("?.!,;: ")
        proposed = tail[:120] if tail else None
    if not proposed or len(proposed) < 3:
        return None
    return {
        "trigger": "regex_match",
        "proposed": proposed,
        "rationale": f"Detected pivot phrase in your message; offering as a"
                       f" candidate seed update.",
        "confidence": 0.4,
        "auto_update": False,
    }


def _copilot_seed_suggestion(user_message: str,
                              current_seed: Optional[str],
                              brief: str) -> Optional[Dict[str, Any]]:
    """Layer B: deterministic copilot proposal. Inspects the user's message
    against the current seed; if there's a strong pivot signal that the
    regex safety net would also catch, surface it with higher confidence.

    Without an LLM in the loop we can't do nuanced pivot detection — so
    this layer reuses the regex result and bumps confidence when:
      - the proposed seed is materially different from the current seed
      - the user's message contains topic words not in the current seed
    """
    base = _regex_seed_pivot(user_message)
    if not base:
        return None
    proposed = base["proposed"]
    # Bump confidence based on dissimilarity with current seed.
    cur = (current_seed or "").lower()
    proposed_l = proposed.lower()
    if cur and proposed_l != cur:
        cur_toks = set(re.findall(r"[a-z0-9]+", cur))
        new_toks = set(re.findall(r"[a-z0-9]+", proposed_l))
        if cur_toks and new_toks:
            overlap = len(cur_toks & new_toks) / max(1, len(cur_toks | new_toks))
            if overlap < 0.3:
                base["trigger"] = "user_pivoted"
                base["confidence"] = 0.7
                base["rationale"] = (
                    f"You've shifted focus from \"{current_seed}\" to "
                    f"\"{proposed}\". Update the seed to re-rank findings?"
                )
    return base


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Chat with Aurora, grounded in the current run.

    Body::
        {"message": "what's the most surprising finding?",
         "run_dir": "C:\\path\\to\\run"   (optional; defaults to latest)
        }
    Response::
        {"ok": true, "text": "...", "citations": [...], "model": "...", "grounded": true,
         "seed_suggestion": {"trigger", "proposed", "rationale", "confidence"} | null}
    """
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "empty message"}), 400

    # Resolve run_dir → state for grounding.
    run_dir_arg = body.get("run_dir")
    rd = Path(run_dir_arg) if run_dir_arg else (find_latest_run(DEFAULT_OUTPUTS) if _HAVE_STATE else None)

    state: Dict[str, Any] = {}
    citations: list = []
    if rd and rd.exists() and _HAVE_STATE:
        try:
            state = build_state(rd)
            citations = ["deep_math.json", "report.json", "structure_contract.json"]
        except Exception as e:
            state = {"_assembly_error": str(e)}

    brief = _build_grounding_brief(state)

    # Atom 3.4: pull the current seed from run_meta. Prepend to the prompt
    # so the copilot answers WITHIN the user's stated focus.
    current_seed = (state.get("run_meta") or {}).get("seed_text")
    seed_preamble = ""
    if current_seed:
        seed_preamble = (
            f"\n=== USER FOCUS ===\nThe user originally said they were "
            f"trying to figure out: \"{current_seed}\". Answer their "
            f"current question while staying grounded in the analysis brief.\n"
        )

    # Layer B + C: detect potential seed pivot in the user's message.
    seed_suggestion = _copilot_seed_suggestion(message, current_seed, brief)

    # Auto-detect Ollama; fall back gracefully if unreachable.
    if not _ollama_available(DEFAULT_OLLAMA_URL, timeout=1.5):
        return jsonify({
            "ok": True,
            "text": _deterministic_chat_response(message, brief),
            "citations": citations,
            "model": "deterministic-fallback",
            "ollama_url_attempted": DEFAULT_OLLAMA_URL,
            "ollama_model_attempted": DEFAULT_OLLAMA_MODEL,
            "grounded": True,
            "ollama_reachable": False,
            "seed_suggestion": seed_suggestion,
        })

    # ---- Brief-4 RAG copilot — retrieve grounding entries from the
    # knowledge bank for the user's question + current run context, and
    # include them in the prompt so the copilot can ground claims to
    # specific entries. Falls back to plain (analysis-brief-only) prompt
    # when the KB has no relevant entries OR the feature flag is off.
    rag_entries: List[Dict[str, Any]] = []
    rag_block = ""
    try:
        from fantasyai.aurora.synthesis.rag import synthesis_mode
        if synthesis_mode() in ("rag", "auto"):
            from fantasyai.aurora.knowledge_bank import retrieve, RetrievalMode
            from fantasyai.aurora.knowledge_bank.retrieval.engine import (
                _domains_from_state,
            )
            domains = _domains_from_state(state) or None
            entries = retrieve(message, mode=RetrievalMode.HYBRID,
                                  domains=domains, max_results=8)
            if entries:
                rag_block_lines = ["", "=== KNOWLEDGE BANK ENTRIES ==="]
                for r in entries:
                    e = r.entry
                    rag_block_lines.append(f"--- {e.id} — {e.name} ---")
                    rag_block_lines.append(f"Definition: {e.definition[:400]}")
                    if e.mechanism:
                        rag_block_lines.append(f"Mechanism: {e.mechanism[:400]}")
                    if e.references:
                        cit = (getattr(e.references[0], "citation", None)
                                or (e.references[0].get("citation")
                                     if isinstance(e.references[0], dict)
                                     else ""))
                        if cit: rag_block_lines.append(f"Reference: {cit}")
                    rag_block_lines.append("")
                rag_block = "\n".join(rag_block_lines)
                rag_entries = [
                    {"id": r.entry.id, "name": r.entry.name,
                      "relevance": float(r.relevance),
                      "matched_via": list(r.matched_via)}
                    for r in entries
                ]
    except Exception:
        rag_block = ""
        rag_entries = []

    # Real Ollama call.
    #
    # Voice rule: when the brief includes a HEADLINE SYNTHESIS block at the
    # top, that prose was generated deterministically by Aurora's synthesis
    # engine and represents the canonical human-readable framing. The
    # copilot should answer in the same voice — connected sentences, plain
    # English, mechanism-aware — rather than dry "finding 1: ..." citations.
    system = (
        "You are Aurora, a quantitative-analysis copilot. Answer the user's question "
        "using ONLY the structured findings below from the current analysis run "
        + ("AND the knowledge-bank entries also provided. " if rag_block
            else ". ")
        + "If the answer is not in the findings or knowledge entries, say so "
          "explicitly — do not invent. "
        "Keep responses under 200 words. Prefer specific numbers over generalities. "
        "When citing a finding, name the artifact it came from "
        "(deep_math.json, report.json, motif.json, etc). "
        + ("When citing a knowledge-bank entry, put its id in [brackets] "
            "after the claim, e.g. [seed:damped_harmonic_oscillator]. "
            if rag_block else "")
        + "\nVOICE: If the brief opens with a HEADLINE SYNTHESIS block, mirror "
        "that voice — write in connected sentences, plain English, and "
        "explain mechanism rather than enumerate findings. Treat the headline "
        "synthesis as the canonical framing; your reply should feel like a "
        "natural continuation of it, not a separate machine-readable digest."
        f"{seed_preamble}\n\n"
        f"=== ANALYSIS BRIEF ===\n{brief}\n=== END BRIEF ==="
        + rag_block
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": message},
    ]
    result = _ollama_chat(messages, timeout=int(os.environ.get("AURORA_CHAT_TIMEOUT", "90")))

    if not result["ok"]:
        # Surface the failure but keep the deterministic content as the answer.
        return jsonify({
            "ok": True,
            "text": _deterministic_chat_response(message, brief)
                   + f"\n\n(LLM call failed: {result['error']})",
            "citations": citations,
            "model": result["model"],
            "grounded": True,
            "ollama_reachable": True,
            "llm_error": result["error"],
            "seed_suggestion": seed_suggestion,
        })

    return jsonify({
        "ok": True,
        "text": result["text"],
        "citations": citations,
        "model": result["model"],
        "grounded": True,
        "ollama_reachable": True,
        "seed_suggestion": seed_suggestion,
        # Brief-4 RAG: surface the knowledge entries that grounded the
        # response so the frontend can render provenance chips/popups.
        "knowledge_entries": rag_entries,
        "rag_mode": "rag" if rag_entries else "no_kb_match",
    })


# ---------------------------------------------------------------------------
# Glass-box: provenance ledger + per-finding explainers
# ---------------------------------------------------------------------------

def _resolve_run_dir_from_query() -> Optional[Path]:
    """Pull run_dir from query string, defaulting to the latest run."""
    rd_arg = request.args.get("run_dir")
    if rd_arg:
        return Path(rd_arg)
    if _HAVE_STATE:
        latest = find_latest_run(DEFAULT_OUTPUTS)
        if latest:
            return latest
    return None


@app.route("/api/provenance/<claim_id>")
def api_provenance(claim_id: str):
    """Return a single ledger entry by claim_id.

    Query: ?run_dir=... (optional; defaults to latest run)
    """
    if not _HAVE_PROVENANCE:
        return jsonify({"ok": False, "error": "provenance module unavailable"}), 503
    rd = _resolve_run_dir_from_query()
    if not rd or not rd.exists():
        return jsonify({"ok": False, "error": "no run_dir resolved"}), 404
    # Lazy-build ledger if it doesn't exist yet (older runs).
    if not (rd / "_provenance_ledger.jsonl").exists() and build_provenance_for_run:
        try:
            build_provenance_for_run(rd, write=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"ledger build failed: {e}"}), 500
    entry = load_provenance_entry(rd, claim_id)
    if not entry:
        return jsonify({"ok": False, "error": f"claim_id '{claim_id}' not found",
                        "run_dir": str(rd)}), 404
    return jsonify({"ok": True, "run_dir": str(rd), "entry": entry})


@app.route("/api/explainer/<claim_type>/<int:index>")
def api_explainer(claim_type: str, index: int):
    """Return a structured explanation for a claim.

    Routes:
      /api/explainer/anomaly/<index>
      /api/explainer/regime/<index>

    Query: ?run_dir=... (optional)
    """
    if not _HAVE_EXPLAINERS:
        return jsonify({"ok": False, "error": "explainer module unavailable"}), 503
    rd = _resolve_run_dir_from_query()
    if not rd or not rd.exists():
        return jsonify({"ok": False, "error": "no run_dir resolved"}), 404

    deep = None
    try:
        dm_path = rd / "deep_math.json"
        if dm_path.exists():
            deep = json.loads(dm_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"could not read deep_math.json: {e}"}), 500
    if not deep:
        return jsonify({"ok": False, "error": "deep_math.json missing or unreadable"}), 404

    try:
        if claim_type == "anomaly":
            points = ((deep.get("extensions") or {}).get("anomaly_attribution") or {}).get("points") or []
            if not (0 <= index < len(points)):
                return jsonify({"ok": False, "error": f"anomaly index {index} out of range (0–{len(points)-1})"}), 404
            structure = None
            sc = rd / "structure_contract.json"
            if sc.exists():
                structure = json.loads(sc.read_text(encoding="utf-8", errors="replace"))
            explanation = explain_anomaly(points[index], structure=structure, all_points=points)
            return jsonify({"ok": True, "run_dir": str(rd), "explanation": explanation})

        if claim_type == "regime":
            regimes_block = deep.get("regimes") or {}
            stats = regimes_block.get("stats") or []
            if not (0 <= index < len(stats)):
                return jsonify({"ok": False, "error": f"regime index {index} out of range (0–{len(stats)-1})"}), 404
            explanation = explain_regime(regimes_block, segment_index=index)
            return jsonify({"ok": True, "run_dir": str(rd), "explanation": explanation})

        if claim_type == "causal":
            causal_block = deep.get("causal_what_if") or {}
            structure = None
            sc = rd / "structure_contract.json"
            if sc.exists():
                structure = json.loads(sc.read_text(encoding="utf-8", errors="replace"))
            explanation = explain_causal(causal_block, structure=structure)
            return jsonify({"ok": True, "run_dir": str(rd), "explanation": explanation})

        if claim_type == "physics":
            pd_block = deep.get("physics_discovery") or {}
            phys_diag = deep.get("physics") or {}
            explanation = explain_physics(pd_block, physics_diag=phys_diag)
            return jsonify({"ok": True, "run_dir": str(rd), "explanation": explanation})

        return jsonify({"ok": False, "error": f"explainer type '{claim_type}' not implemented"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": f"explainer failed: {type(e).__name__}: {e}"}), 500


# ---------------------------------------------------------------------------
# Live What-If Engine — natural language intervention → simulated trajectory
# ---------------------------------------------------------------------------

@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    """Run a what-if intervention against the current run's fitted models.

    Body::
        {
          "text": "if precip_mm increases by 5 for 12 hours",   ← natural language
          "run_dir": "..."                                       ← optional; default = latest
        }

      OR (structured form for repeatable / programmatic queries):
        {
          "spec": {"variable": "precip_mm", "kind": "delta", "value": 5.0, "duration_steps": 12},
          "run_dir": "..."
        }

    Response::
        {
          "ok": true,
          "intervention": {...},                  ← echoed back; UI must show this for confirmation
          "parser_used": "regex" | "llm" | "structured",
          "identifiable": bool,
          "identifiability": {...},               ← approach, satisfied_if, violated_if, caveats
          "trajectory": [{step, value, ci_low, ci_high}],
          "delta_target": float | null,
          "method": "linear_ols_effect_lookup" | "physics_forward_sim_<name>" | "refused",
          "headline": "..."
        }
    """
    if not _HAVE_WHATIF:
        return jsonify({"ok": False, "error": "what-if module unavailable"}), 503
    if not _HAVE_STATE:
        return jsonify({"ok": False, "error": "state_builder unavailable"}), 503

    body = request.get_json(force=True, silent=True) or {}
    rd_arg = body.get("run_dir")
    rd = Path(rd_arg) if rd_arg else (find_latest_run(DEFAULT_OUTPUTS) if _HAVE_STATE else None)
    if not rd or not rd.exists():
        return jsonify({"ok": False, "error": "no run_dir resolved"}), 404

    # Read deep_math + structure for identifiability + simulation.
    deep = None
    structure = None
    try:
        dm_path = rd / "deep_math.json"
        if dm_path.exists():
            deep = json.loads(dm_path.read_text(encoding="utf-8", errors="replace"))
        sc_path = rd / "structure_contract.json"
        if sc_path.exists():
            structure = json.loads(sc_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"failed to read artifacts: {e}"}), 500

    # Build the intervention spec — either from structured input or by parsing text.
    parser_used = "structured"
    spec: Optional[InterventionSpec] = None
    if isinstance(body.get("spec"), dict):
        try:
            s = body["spec"]
            spec = InterventionSpec(
                variable=str(s["variable"]),
                kind=str(s.get("kind", "delta")),
                value=float(s.get("value", 1.0)),
                duration_steps=s.get("duration_steps"),
                outcome=s.get("outcome"),
                raw_text=str(s.get("raw_text", "")),
            )
        except Exception as e:
            return jsonify({"ok": False, "error": f"invalid spec: {e}"}), 400
    else:
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "missing 'text' or 'spec'"}), 400
        # Pull available columns + dt_seconds to give the parser context.
        cols = []
        if structure:
            cols = [c.get("name") for c in (structure.get("columns") or [])
                    if isinstance(c, dict) and c.get("name")]
        dt = (structure or {}).get("dt_seconds")
        spec, parser_used = parse_intervention_text(
            text,
            available_columns=cols,
            dt_seconds=dt,
        )
        if not spec:
            return jsonify({
                "ok": False, "parser_used": "none",
                "error": "could not parse intervention from text",
                "hint": "try a structured form like: 'precip_mm +5' or 'set wind_speed to 25'",
            }), 400

    # Simulate.
    horizon = int(body.get("horizon_steps") or 24)
    try:
        result = simulate_intervention(
            spec, deep_math=deep, structure=structure, horizon_steps=horizon,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"simulation failed: {type(e).__name__}: {e}"}), 500

    # Compose response.
    response = {
        "ok": True,
        "run_dir": str(rd),
        "parser_used": parser_used,
        "intervention": spec.to_dict(),
    }
    response.update(result)   # adds: identifiable, identifiability, trajectory, delta_target, method, headline
    return jsonify(response)


# ---------------------------------------------------------------------------
# W9: Export & Reproducibility
# ---------------------------------------------------------------------------

@app.route("/api/export")
def api_export():
    """Generate + serve an export from the current run.

    Query:
      format   : "findings_md" | "methods_md" | "bundle_json" | "all" (default findings_md)
      run_dir  : optional; defaults to latest run
      domain   : optional; applies domain re-skinning to findings.md only
      download : optional truthy; if 1, returns the file as an attachment

    Default response: returns the JSON listing the generated paths. With
    ``download=1`` (or via the ``/api/export/<format>/file`` route below),
    the file itself is streamed.
    """
    if not _HAVE_EXPORT:
        return jsonify({"ok": False, "error": "export module unavailable"}), 503

    rd_arg = request.args.get("run_dir")
    rd = Path(rd_arg) if rd_arg else (find_latest_run(DEFAULT_OUTPUTS) if _HAVE_STATE else None)
    if not rd or not rd.exists():
        return jsonify({"ok": False, "error": "no run_dir resolved"}), 404

    fmt = (request.args.get("format") or "findings_md").lower()
    domain = request.args.get("domain")

    try:
        if fmt == "all":
            paths = export_all(rd, domain_id=domain)
            return jsonify({"ok": True, "run_dir": str(rd), "exports": paths})
        if fmt == "findings_md":
            p = export_findings_markdown(rd, domain_id=domain)
        elif fmt == "methods_md":
            p = export_methods_markdown(rd)
        elif fmt == "bundle_json":
            p = export_reproducibility_bundle(rd)
        else:
            return jsonify({"ok": False, "error": f"unknown format: {fmt}",
                            "supported": ["findings_md", "methods_md", "bundle_json", "all"]}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"export failed: {type(e).__name__}: {e}"}), 500

    # Optional inline download.
    if request.args.get("download") in ("1", "true", "yes"):
        return send_file(str(p), as_attachment=True, download_name=Path(p).name)

    # JSON metadata response.
    return jsonify({"ok": True, "run_dir": str(rd), "format": fmt,
                    "path": str(p), "size_bytes": Path(p).stat().st_size})


@app.route("/api/export/<format_id>/file")
def api_export_file(format_id: str):
    """Return the export file as a download attachment.

    Convenience route so the frontend can do ``window.open('/api/export/findings_md/file')``
    and trigger a download directly without a JSON intermediate.
    """
    if not _HAVE_EXPORT:
        abort(503)
    rd_arg = request.args.get("run_dir")
    rd = Path(rd_arg) if rd_arg else (find_latest_run(DEFAULT_OUTPUTS) if _HAVE_STATE else None)
    if not rd or not rd.exists():
        abort(404)
    domain = request.args.get("domain")
    try:
        if format_id == "findings_md":
            p = export_findings_markdown(rd, domain_id=domain)
        elif format_id == "methods_md":
            p = export_methods_markdown(rd)
        elif format_id == "bundle_json":
            p = export_reproducibility_bundle(rd)
        else:
            abort(404)
    except Exception:
        abort(500)
    return send_file(str(p), as_attachment=True, download_name=Path(p).name)


@app.route("/api/symbolic_regression", methods=["POST"])
def api_symbolic_regression():
    """Discover novel equations + conservation laws from the run's source data.

    Body (all optional):
      run_dir  : explicit run directory; defaults to latest
      max_features: 1 or 2 (default 2 — enables Pierson-Moskowitz / allometric forms)
      top_k    : results returned per category (default 5)
      max_cv   : conservation-law strictness threshold (default 0.05)

    Loads the source CSV from the resolved contract, runs:
      * symbolic_discovery — fits 12 curated forms, returns top-k by AIC
      * conservation — scans subsets for direct/sum/ratio/quadratic/linear invariants

    Caches result as ``<run_dir>/symbolic_discovery.json`` for instant replay.
    """
    if not _HAVE_SR or not _HAVE_CONSERVATION:
        return jsonify({"ok": False, "error": "symbolic_discovery / conservation modules unavailable"}), 503

    body = request.get_json(force=True, silent=True) or {}
    rd_arg = body.get("run_dir")
    rd = Path(rd_arg) if rd_arg else (find_latest_run(DEFAULT_OUTPUTS) if _HAVE_STATE else None)
    if not rd or not rd.exists():
        return jsonify({"ok": False, "error": "no run_dir resolved"}), 404

    # Cache hit?
    cached_path = rd / "symbolic_discovery.json"
    if cached_path.exists() and not body.get("force"):
        try:
            cached = json.loads(cached_path.read_text(encoding="utf-8"))
            return jsonify({"ok": True, "run_dir": str(rd), "cached": True, **cached})
        except Exception:
            pass  # fall through to regenerate

    # Resolve source dataset path from tableselection.json or _RESOLVED_CONTRACT.
    table_sel = json.loads((rd / "tableselection.json").read_text(encoding="utf-8")) \
        if (rd / "tableselection.json").exists() else {}
    source_path = table_sel.get("table")
    if not source_path or not Path(source_path).exists():
        return jsonify({"ok": False, "error": "source dataset path not resolvable"}), 400

    # Resolve target column from the run's contract.
    resolved = json.loads((rd / "_RESOLVED_CONTRACT.json").read_text(encoding="utf-8")) \
        if (rd / "_RESOLVED_CONTRACT.json").exists() else {}
    target_col = resolved.get("target_col")
    if not target_col:
        return jsonify({"ok": False, "error": "no target_col in resolved contract"}), 400

    # Load source data via the multi-format loader.
    if not _HAVE_LOADER:
        return jsonify({"ok": False, "error": "loader unavailable"}), 503
    try:
        load_result = load_dataset(Path(source_path))
        df = load_result.df
    except Exception as e:
        return jsonify({"ok": False, "error": f"failed to load source: {e}"}), 500

    if target_col not in df.columns:
        return jsonify({"ok": False, "error": f"target_col '{target_col}' not in dataset"}), 400

    max_features = int(body.get("max_features", 2))
    top_k = int(body.get("top_k", 5))
    max_cv = float(body.get("max_cv", 0.05))

    # 1) Symbolic discovery — fit curated forms, rank by AIC.
    sr_results: list = []
    sr_features_used: list = []
    try:
        sr_results = discover_relation(df, target_col, max_features=max_features, top_k=top_k)
        if sr_results:
            sr_features_used = sr_results[0].get("features", [])
    except Exception as e:
        return jsonify({"ok": False, "error": f"symbolic_discovery failed: {e}"}), 500

    # 2) Conservation laws — scan numeric columns for invariants.
    try:
        import numpy as np
        numeric_cols = [c for c in df.columns
                        if np.issubdtype(df[c].dtype, np.number)]
        invariants = detect_invariants(df, numeric_cols=numeric_cols,
                                        max_cv=max_cv, top_k=top_k)
    except Exception as e:
        invariants = []

    payload = {
        "target_col": target_col,
        "source_path": source_path,
        "n_rows": int(df.shape[0]),
        "n_cols": int(df.shape[1]),
        "symbolic_relations": sr_results,
        "invariants": invariants,
        "feature_columns_used": sr_features_used,
        "max_features": max_features,
        "max_cv": max_cv,
    }

    # Cache to disk for instant replay.
    try:
        cached_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass

    return jsonify({"ok": True, "run_dir": str(rd), "cached": False, **payload})


# ---------------------------------------------------------------------------
# Sprint B: Public-data connectors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Atom 4.3 + 4.4: Public /learning page + admin review + RSS/JSON feeds
# ---------------------------------------------------------------------------
# /learning             — public read-only page; serves from published/<date>.json
# /learning/admin       — token-gated review dashboard (drafts → published)
# /api/learning/changelog — JSON of one date or the most recent
# /api/learning/library   — cumulative library state
# /api/learning/feed.rss  — RSS feed (published only)
# /api/learning/feed.json — JSON Feed format (published only)
# /api/learning/admin/publish — POST: maintainer publishes a date's draft
# /api/learning/admin/reject  — POST: maintainer rejects a specific event

LEARNING_ADMIN_TOKEN_ENV = "AURORA_ADMIN_TOKEN"


def _learning_paths():
    """Lazy-import — keeps studio_api importable when scheduler is missing."""
    try:
        from fantasyai.aurora.scheduler.changelog import (
            DEFAULT_CHANGELOG_ROOT, DEFAULT_PRIORS_ROOT,
            DRAFT_DIR_NAME, PUBLISHED_DIR_NAME,
            library_summary as _lib_summary,
            list_priors as _list_priors,
        )
        return {
            "log_root": Path(DEFAULT_CHANGELOG_ROOT),
            "priors_root": Path(DEFAULT_PRIORS_ROOT),
            "draft": DRAFT_DIR_NAME,
            "published": PUBLISHED_DIR_NAME,
            "summary_fn": _lib_summary,
            "list_fn": _list_priors,
        }
    except Exception:
        return None


def _admin_token_ok() -> bool:
    """Maintainer authentication via env var. Header: X-Aurora-Admin-Token."""
    expected = os.environ.get(LEARNING_ADMIN_TOKEN_ENV)
    if not expected:
        # No token configured → admin endpoints are locked.
        return False
    got = request.headers.get("X-Aurora-Admin-Token") or \
          request.args.get("admin_token")
    return bool(got) and got == expected


def _list_changelog_dates(log_root: Path, subdir: str) -> list:
    p = log_root / subdir
    if not p.exists():
        return []
    out = []
    for f in p.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "date": d.get("date") or f.stem,
                "n_events": len(d.get("events") or []),
                "published": d.get("published", subdir == "published"),
                "path": str(f),
            })
        except Exception:
            continue
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


@app.route("/api/learning/library")
def api_learning_library():
    """Cumulative library state — counts + per-domain breakdown."""
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False, "error": "scheduler module unavailable"}), 200
    summary = paths["summary_fn"](paths["priors_root"])
    return jsonify({"ok": True, "library": summary})


@app.route("/api/learning/changelog")
def api_learning_changelog():
    """Return one published changelog entry (?date=YYYY-MM-DD) or the latest."""
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False, "error": "scheduler module unavailable"}), 200
    log_root = paths["log_root"]
    pub = log_root / paths["published"]
    if not pub.exists():
        return jsonify({"ok": True, "changelog": None,
                        "reason": "no published entries yet"})
    date = request.args.get("date")
    if date:
        target = pub / f"{date}.json"
        if not target.exists():
            return jsonify({"ok": True, "changelog": None,
                            "reason": f"no published entry for {date}"})
    else:
        files = sorted(pub.glob("*.json"), reverse=True)
        if not files:
            return jsonify({"ok": True, "changelog": None})
        target = files[0]
    try:
        return jsonify({"ok": True,
                        "changelog": json.loads(target.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/learning/changelog/list")
def api_learning_changelog_list():
    """List all published changelog dates."""
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False, "error": "scheduler module unavailable"}), 200
    return jsonify({
        "ok": True,
        "entries": _list_changelog_dates(paths["log_root"], paths["published"]),
    })


@app.route("/api/learning/feed.rss")
def api_learning_feed_rss():
    """RSS feed of published changelog entries."""
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False}), 503
    pub = paths["log_root"] / paths["published"]
    items = []
    if pub.exists():
        files = sorted(pub.glob("*.json"), reverse=True)[:30]
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                date = d.get("date") or f.stem
                events = d.get("events") or []
                summary_lines = [
                    f"<li>{e.get('kind','?').upper()} · {e.get('display_name','')} · {e.get('delta','')}</li>"
                    for e in events[:10]
                ]
                items.append(
                    f"<item><title>Aurora learning — {date}</title>"
                    f"<link>https://aurora.local/learning?date={date}</link>"
                    f"<pubDate>{date}T00:00:00Z</pubDate>"
                    f"<description><![CDATA[<ul>{''.join(summary_lines)}</ul>]]></description>"
                    f"</item>"
                )
            except Exception:
                continue
    rss = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" ?>\n"
        "<rss version=\"2.0\"><channel>"
        "<title>Aurora · Learning Journal</title>"
        "<link>https://aurora.local/learning</link>"
        "<description>What Aurora learned overnight from trusted public sources.</description>"
        + "".join(items) +
        "</channel></rss>"
    )
    return rss, 200, {"Content-Type": "application/rss+xml; charset=utf-8"}


@app.route("/api/learning/feed.json")
def api_learning_feed_json():
    """JSON Feed (jsonfeed.org) of published changelog entries."""
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False}), 503
    pub = paths["log_root"] / paths["published"]
    items = []
    if pub.exists():
        for f in sorted(pub.glob("*.json"), reverse=True)[:30]:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                date = d.get("date") or f.stem
                events = d.get("events") or []
                content = "\n".join(
                    f"- **{e.get('kind','').upper()}** · "
                    f"{e.get('display_name','')} · {e.get('delta','')}"
                    for e in events
                )
                items.append({
                    "id": f"aurora-learning-{date}",
                    "title": f"Aurora learning — {date}",
                    "url": f"/learning?date={date}",
                    "date_published": f"{date}T00:00:00Z",
                    "content_text": content,
                })
            except Exception:
                continue
    return jsonify({
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Aurora · Learning Journal",
        "home_page_url": "/learning",
        "feed_url": "/api/learning/feed.json",
        "items": items,
    })


@app.route("/api/learning/admin/list_drafts")
def api_learning_admin_list_drafts():
    """Token-gated. List all draft changelog dates awaiting review."""
    if not _admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False, "error": "scheduler module unavailable"}), 200
    return jsonify({
        "ok": True,
        "drafts": _list_changelog_dates(paths["log_root"], paths["draft"]),
    })


@app.route("/api/learning/admin/draft")
def api_learning_admin_draft():
    """Token-gated. Read one draft changelog (?date=YYYY-MM-DD)."""
    if not _admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False, "error": "scheduler module unavailable"}), 200
    date = request.args.get("date")
    if not date:
        return jsonify({"ok": False, "error": "date required"}), 400
    p = paths["log_root"] / paths["draft"] / f"{date}.json"
    if not p.exists():
        return jsonify({"ok": False, "error": f"no draft for {date}"}), 404
    return jsonify({"ok": True, "draft": json.loads(p.read_text(encoding="utf-8"))})


@app.route("/api/learning/admin/publish", methods=["POST"])
def api_learning_admin_publish():
    """Token-gated. Move draft/<date>.json → published/<date>.json.

    Body::

        {
          "date": "2026-05-06",
          "approved_event_indices": [0, 1, 3],   # default: all
          "rejected_event_indices": [2],
          "edits": {"2": {"rationale": "rewritten text"}}
        }
    """
    if not _admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    paths = _learning_paths()
    if paths is None:
        return jsonify({"ok": False, "error": "scheduler module unavailable"}), 200
    body = request.get_json(silent=True) or {}
    date = body.get("date")
    if not date:
        return jsonify({"ok": False, "error": "date required"}), 400
    draft_p = paths["log_root"] / paths["draft"] / f"{date}.json"
    if not draft_p.exists():
        return jsonify({"ok": False, "error": f"no draft for {date}"}), 404
    try:
        d = json.loads(draft_p.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"ok": False, "error": f"draft parse failed: {e}"}), 500

    events = d.get("events") or []
    approved = body.get("approved_event_indices")
    rejected = set(body.get("rejected_event_indices") or [])
    edits = body.get("edits") or {}

    if approved is None:
        approved = [i for i in range(len(events)) if i not in rejected]
    approved_set = set(approved)
    final_events = []
    for i, e in enumerate(events):
        if i in rejected:
            continue
        if i not in approved_set:
            continue
        # Apply edits if any.
        if str(i) in edits:
            for k, v in (edits[str(i)] or {}).items():
                if k in ("rationale", "delta", "display_name"):
                    e[k] = v
        final_events.append(e)

    pub_dir = paths["log_root"] / paths["published"]
    pub_dir.mkdir(parents=True, exist_ok=True)
    pub_path = pub_dir / f"{date}.json"
    published = {
        **d,
        "events": final_events,
        "n_events_drafted": len(events),
        "n_events_published": len(final_events),
        "n_events_rejected": len(rejected),
        "review_required": False,
        "published": True,
        "published_at": datetime.utcnow().isoformat() + "Z",
    }
    pub_path.write_text(json.dumps(published, indent=2, default=str),
                         encoding="utf-8")
    return jsonify({
        "ok": True,
        "published_path": str(pub_path),
        "n_events_published": len(final_events),
        "n_events_rejected": len(rejected),
    })


@app.route("/learning")
def public_learning_page():
    """Atom 4.4: Public read-only learning journal. Plain HTML; no auth."""
    paths = _learning_paths()
    pub_entries = []
    summary = {"total": {"canonical": 0, "candidate": 0, "deprecated": 0},
                "by_domain": {}}
    if paths is not None:
        pub_entries = _list_changelog_dates(paths["log_root"], paths["published"])
        summary = paths["summary_fn"](paths["priors_root"])
    rows = ""
    for e in pub_entries[:20]:
        rows += (f'<li><a href="?date={e["date"]}">{e["date"]}</a> '
                  f'<span style="color:#888">· {e["n_events"]} events</span></li>')
    domain_rows = ""
    for d, counts in (summary.get("by_domain") or {}).items():
        domain_rows += (f"<tr><td>{d}</td>"
                          f"<td>{counts.get('canonical',0)}</td>"
                          f"<td>{counts.get('candidate',0)}</td>"
                          f"<td>{counts.get('deprecated',0)}</td></tr>")
    # Headline: latest changelog date if any.
    latest_html = "<p>No published entries yet — check back after the next overnight pass + maintainer review.</p>"
    if pub_entries:
        latest_date = request.args.get("date") or pub_entries[0]["date"]
        try:
            latest_path = paths["log_root"] / paths["published"] / f"{latest_date}.json"
            d = json.loads(latest_path.read_text(encoding="utf-8"))
            ev_lines = ""
            for ev in (d.get("events") or []):
                ev_lines += (f'<div class="event"><b>{ev.get("kind","").upper()}</b> · '
                               f'{ev.get("display_name","")} <span style="color:#888">'
                               f'· {ev.get("delta","")}</span><br/>'
                               f'<small>{ev.get("rationale","")}</small></div>')
            latest_html = (f"<h2>Aurora learning · {latest_date}</h2>"
                            f"{ev_lines or '<p>(no events)</p>'}")
        except Exception:
            pass
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Aurora · Learning Journal</title>
<style>
body {{ font-family: 'JetBrains Mono', monospace; background: #0d0820;
       color: #d8d4ec; padding: 30px; max-width: 900px; margin: 0 auto; }}
h1 {{ color: #6ee7ff; letter-spacing: 2px; }}
h2 {{ color: #88ffd1; margin-top: 30px; }}
.event {{ padding: 10px 14px; margin: 10px 0; background: rgba(110,231,255,0.04);
          border-left: 3px solid #6ee7ff; border-radius: 2px; }}
.event b {{ color: #6ee7ff; }}
table {{ border-collapse: collapse; margin: 10px 0; }}
th, td {{ padding: 6px 12px; text-align: left; border-bottom: 1px solid #2a2540; }}
a {{ color: #6ee7ff; text-decoration: none; }} a:hover {{ text-decoration: underline; }}
.feeds {{ margin: 14px 0; font-size: 11px; }}
.feeds a {{ margin-right: 12px; padding: 4px 10px; border: 1px solid #6ee7ff;
            border-radius: 2px; }}
</style></head><body>
<h1>AURORA · LEARNING JOURNAL</h1>
<p>What Aurora learned overnight from trusted public sources.</p>
<div class="feeds">
  <a href="/api/learning/feed.rss">RSS</a>
  <a href="/api/learning/feed.json">JSON Feed</a>
  <a href="/api/learning/library">Library state</a>
</div>
{latest_html}
<h2>Cumulative library</h2>
<table><thead><tr><th>Domain</th><th>Canonical</th><th>Candidate</th><th>Deprecated</th></tr></thead>
<tbody>{domain_rows or '<tr><td colspan="4">empty library</td></tr>'}</tbody></table>
<p><b>Total:</b> {summary['total'].get('canonical',0)} canonical ·
   {summary['total'].get('candidate',0)} candidate ·
   {summary['total'].get('deprecated',0)} deprecated</p>
<h2>Earlier entries</h2>
<ul>{rows or '<li>none</li>'}</ul>
</body></html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/connectors")
def api_connectors():
    """List every loaded connector with metadata (id, domain, key requirements)."""
    if not _HAVE_CONNECTORS:
        return jsonify({"ok": False, "error": "connectors module unavailable",
                        "connectors": []}), 503
    return jsonify({"ok": True, "connectors": list_data_connectors()})


@app.route("/api/connectors/<connector_id>/search")
def api_connector_search(connector_id: str):
    """Search a connector for matching datasets — returns previews, not data."""
    if not _HAVE_CONNECTORS:
        return jsonify({"ok": False, "error": "connectors module unavailable"}), 503
    conn = get_data_connector(connector_id)
    if conn is None:
        return jsonify({"ok": False, "error": f"unknown connector: {connector_id}"}), 404
    if conn.requires_api_key and not conn.has_api_key():
        return jsonify({
            "ok": False,
            "error": "api_key_missing",
            "message": f"Set the {conn.api_key_env_var} environment variable to use this connector.",
            "homepage_url": conn.homepage_url,
        }), 400
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", 10))
    except Exception:
        limit = 10
    try:
        results = conn.search(query, limit=limit)
        return jsonify({
            "ok": True,
            "connector": conn.id,
            "query": query,
            "results": [r.to_dict() for r in results],
        })
    except Exception as e:
        return jsonify({
            "ok": False, "connector": conn.id,
            "error": f"search failed: {type(e).__name__}: {e}",
        }), 500


@app.route("/api/connectors/<connector_id>/fetch", methods=["POST"])
def api_connector_fetch(connector_id: str):
    """Fetch a dataset from a connector. Body::

        {"item_id": "<connector-specific id>", "force": false, "auto_run": false}

    On success, returns the saved CSV path. If ``auto_run`` is true, also
    triggers an analysis run on the fetched dataset.
    """
    if not _HAVE_CONNECTORS:
        return jsonify({"ok": False, "error": "connectors module unavailable"}), 503
    conn = get_data_connector(connector_id)
    if conn is None:
        return jsonify({"ok": False, "error": f"unknown connector: {connector_id}"}), 404
    if conn.requires_api_key and not conn.has_api_key():
        return jsonify({"ok": False, "error": "api_key_missing"}), 400

    body = request.get_json(force=True, silent=True) or {}
    item_id = body.get("item_id")
    if not item_id:
        return jsonify({"ok": False, "error": "missing item_id"}), 400

    force = bool(body.get("force", False))
    auto_run = bool(body.get("auto_run", False))

    try:
        result = conn.fetch(item_id, force=force)
    except Exception as e:
        return jsonify({"ok": False, "error": f"fetch failed: {type(e).__name__}: {e}"}), 500

    response = {
        "ok": True, "connector": conn.id,
        "item_id": item_id,
        "result": result.to_dict(),
    }

    if auto_run:
        run_result = _trigger_run(dataset=result.csv_path)
        response["run"] = run_result

    return jsonify(response)


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """Record a thumbs-up / thumbs-down on a finding.

    Body: {run_dir, finding_index, finding_title, finding_method?, verdict, note?}
    verdict ∈ {"up", "down", "ignore"}.

    Persists to the global ~/.aurora/learning_store.jsonl so the meta-learner
    can later down-weight priors that consistently get thumbs-down.
    """
    try:
        from fantasyai.aurora.learning import record_feedback
    except Exception as e:
        return jsonify({"ok": False, "error": f"learning module unavailable: {e!s}"}), 503
    body = request.get_json(silent=True) or {}
    try:
        row = record_feedback(
            run_dir=body.get("run_dir") or "",
            finding_index=int(body.get("finding_index") or 0),
            finding_title=body.get("finding_title") or "",
            finding_method=body.get("finding_method"),
            verdict=body.get("verdict") or "",
            note=body.get("note"),
        )
        return jsonify({"ok": True, "row": row})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"feedback persist failed: {e!s}"}), 500


@app.route("/api/learning/recent")
def api_learning_recent():
    """Return the N most-recent learning rows (debug + transparency endpoint)."""
    try:
        from fantasyai.aurora.learning import read_all
    except Exception as e:
        return jsonify({"ok": False, "error": f"learning module unavailable: {e!s}"}), 503
    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50
    rows = read_all()
    rows = sorted(rows, key=lambda r: r.get("ts", ""), reverse=True)[:limit]
    return jsonify({"ok": True, "count": len(rows), "rows": rows})


@app.route("/api/learning/summary")
def api_learning_summary():
    """Return a count of runs ingested + auto-validated + human-marked.

    Used by the topbar "trained on N runs" badge. Cheap; reads the JSONL once.
    """
    try:
        from fantasyai.aurora.learning import auto_feedback_summary
    except Exception as e:
        return jsonify({"ok": False, "error": f"learning module unavailable: {e!s}"}), 503
    try:
        return jsonify({"ok": True, "summary": auto_feedback_summary()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/agents")
def api_agents_list():
    """List the loaded agents with their metadata (Phase 2/3)."""
    try:
        from fantasyai.aurora.agents import default_scheduler
    except Exception as e:
        return jsonify({"ok": False, "error": f"agents unavailable: {e!s}"}), 503
    return jsonify({"ok": True, "agents": default_scheduler().list_agents()})


@app.route("/api/agents/tick", methods=["POST"])
def api_agents_tick():
    """Run all registered agents once (Phase 0 scaffold).

    Body (optional):
      {"task_for": {"agent_csv_url": {"url": "...", "offline": true}, ...}}
    """
    try:
        from fantasyai.aurora.agents import default_scheduler
    except Exception as e:
        return jsonify({"ok": False, "error": f"agents unavailable: {e!s}"}), 503
    body = request.get_json(silent=True) or {}
    task_for = body.get("task_for") if isinstance(body.get("task_for"), dict) else None
    sched = default_scheduler()
    results = sched.tick(task_for=task_for)
    return jsonify({"ok": True,
                    "results": [r.to_dict() for r in results]})


@app.route("/api/agents/runlog")
def api_agents_runlog():
    try:
        from fantasyai.aurora.agents import read_runlog
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50
    return jsonify({"ok": True, "rows": read_runlog(limit=limit)})


@app.route("/api/morning_diary")
def api_morning_diary():
    """Return the morning research diary (Phase 3)."""
    try:
        from fantasyai.aurora.agents import build_morning_diary
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    try:
        hours = int(request.args.get("within_hours", 24))
    except Exception:
        hours = 24
    return jsonify({"ok": True, "diary": build_morning_diary(within_hours=hours)})


@app.route("/api/priors/curation/duplicates")
def api_curation_duplicates():
    try:
        from fantasyai.aurora.priors.curation import find_duplicates
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    try:
        threshold = float(request.args.get("threshold", 0.85))
    except Exception:
        threshold = 0.85
    dups = find_duplicates(threshold=threshold)
    return jsonify({"ok": True, "duplicates": [d.to_dict() for d in dups]})


@app.route("/api/priors/curation/contradictions")
def api_curation_contradictions():
    try:
        from fantasyai.aurora.priors.curation import find_contradictions
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    cs = find_contradictions()
    return jsonify({"ok": True, "contradictions": [c.to_dict() for c in cs]})


@app.route("/api/priors/curation/deprecate", methods=["POST"])
def api_curation_deprecate():
    try:
        from fantasyai.aurora.priors.curation import deprecate
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    body = request.get_json(silent=True) or {}
    pid = body.get("prior_id")
    reason = body.get("reason")
    superseded_by = body.get("superseded_by")
    if not pid or not reason:
        return jsonify({"ok": False, "error": "prior_id + reason required"}), 400
    try:
        row = deprecate(pid, reason=reason, superseded_by=superseded_by)
        return jsonify({"ok": True, "row": row})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/recommendations/outcome", methods=["POST"])
def api_recommendation_outcome():
    """Record the observed outcome of a previously-suggested action (Phase 5).

    Body: {action_id, run_dir, finding_index, outcome ∈ {successful|ineffective|harmful|unknown}, note?}
    """
    try:
        from fantasyai.aurora.recommendations import record_action_outcome
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    body = request.get_json(silent=True) or {}
    try:
        row = record_action_outcome(
            action_id=body.get("action_id", ""),
            run_dir=body.get("run_dir", ""),
            finding_index=int(body.get("finding_index", 0)),
            outcome=body.get("outcome", ""),
            note=body.get("note"),
        )
        return jsonify({"ok": True, "row": row})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/recommendations/actions")
def api_recommendation_actions():
    try:
        from fantasyai.aurora.recommendations import list_actions
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    domain = request.args.get("domain")
    return jsonify({"ok": True,
                    "actions": [a.to_dict() for a in list_actions(domain=domain)]})


@app.route("/api/system_model")
def api_system_model():
    """Return the system model for the latest run (or a specified run_dir)."""
    try:
        from fantasyai.aurora.state_builder import build_state, find_latest_run
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    rd = request.args.get("run_dir")
    if rd:
        run_path = Path(rd)
    else:
        run_path = find_latest_run(DEFAULT_OUTPUTS)
    if not run_path or not run_path.exists():
        return jsonify({"ok": False, "error": "no run available"}), 404
    s = build_state(run_path)
    sm = s.get("system_model") or {}
    return jsonify({"ok": True, "system_model": sm,
                    "run_dir": str(run_path)})


@app.route("/api/templates")
def api_templates_list():
    """List loaded domain templates."""
    try:
        from fantasyai.aurora.system_model import list_templates
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    out = [t.to_dict() for t in list_templates()]
    return jsonify({"ok": True, "templates": out})


@app.route("/api/templates/<template_id>")
def api_template_detail(template_id: str):
    try:
        from fantasyai.aurora.system_model import get_template
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    t = get_template(template_id)
    if t is None:
        return jsonify({"ok": False, "error": "unknown template"}), 404
    return jsonify({"ok": True, "template": t.to_dict()})


@app.route("/api/system_model/intervene", methods=["POST"])
def api_system_model_intervene():
    """Perturb a node and propagate the change through the model.

    Body: {run_dir?, source_entity_id, perturbation, max_depth?}
    Returns: PropagationResult with per-entity {delta, ci_low, ci_high, contributors}.
    """
    try:
        from fantasyai.aurora.system_model import propagate_intervention
        from fantasyai.aurora.state_builder import build_state, find_latest_run
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    body = request.get_json(silent=True) or {}
    rd = body.get("run_dir")
    if rd:
        run = Path(rd)
    else:
        run = find_latest_run(DEFAULT_OUTPUTS)
    if not run or not run.exists():
        return jsonify({"ok": False, "error": "no run available"}), 404
    state = build_state(run)
    sm = state.get("system_model") or {}
    src = body.get("source_entity_id")
    if not src:
        return jsonify({"ok": False, "error": "source_entity_id required"}), 400
    try:
        delta = float(body.get("perturbation") or 0.0)
    except Exception:
        return jsonify({"ok": False, "error": "perturbation must be numeric"}), 400
    max_depth = int(body.get("max_depth") or 3)
    res = propagate_intervention(sm, source_entity_id=src,
                                  perturbation=delta, max_depth=max_depth)
    return jsonify({"ok": True, "result": res.to_dict()})


@app.route("/api/system_model/simulate", methods=["POST"])
def api_system_model_simulate():
    """Forward-step the validated dynamics from the run's last value.

    Body: {run_dir?, n_steps?, ci_pause_threshold?, target_entity_id?}
    Returns: SimResult with per-step {value, ci_low, ci_high, source_equation}.

    PHASE B4 extension: ``target_entity_id`` lets the caller pick which
    entity to simulate. The simulator still requires a fitted law in
    ``physics.discovered_law``; if the requested target doesn't match the
    fitted target, the simulator returns ok=False with an honest error
    rather than silently switching laws.
    """
    try:
        from fantasyai.aurora.system_model import simulate_forward
        from fantasyai.aurora.state_builder import build_state, find_latest_run
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    body = request.get_json(silent=True) or {}
    rd = body.get("run_dir")
    run = Path(rd) if rd else find_latest_run(DEFAULT_OUTPUTS)
    if not run or not run.exists():
        return jsonify({"ok": False, "error": "no run available"}), 404
    state = build_state(run)
    n_steps = int(body.get("n_steps") or 50)
    threshold = float(body.get("ci_pause_threshold") or 5.0)
    target_entity_id = body.get("target_entity_id")
    # Pass the user-picked target through state.forecast.target so the
    # simulator's `_target_entity_id` resolution prefers it. We only
    # overwrite the target string (a hint); we DO NOT swap the discovered
    # law. The simulator handles mismatch honestly.
    if target_entity_id:
        try:
            fc = dict(state.get("forecast") or {})
            # Look up the column for this entity so the simulator can still
            # match the discovered law's target. If the entity has a
            # data_column, prefer that; else use the id.
            ents = ((state.get("system_model") or {}).get("topology") or {}).get("entities") or []
            ent = next((e for e in ents if e.get("id") == target_entity_id), None)
            override = (ent or {}).get("data_column") or target_entity_id
            fc["target"] = override
            state["forecast"] = fc
        except Exception:
            pass
    res = simulate_forward(state, n_steps=n_steps,
                            ci_pause_threshold=threshold)
    return jsonify({"ok": True, "result": res.to_dict()})


@app.route("/api/tier/status")
def api_tier_status():
    """Read current sampling state for a run_dir.

    Query: ?run_dir=<parent_run_dir>  (defaults to latest)
    Returns the SamplingState dict + the tier_runs mapping. Used by the
    frontend to poll while T2/T3 run in the background.
    """
    try:
        from fantasyai.aurora.sampling import SamplingState, read_tier_runs
        from fantasyai.aurora.state_builder import find_latest_run
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    rd = request.args.get("run_dir")
    run_path = Path(rd) if rd else find_latest_run(DEFAULT_OUTPUTS)
    if not run_path or not run_path.exists():
        return jsonify({"ok": False, "error": "no run available"}), 404
    state = SamplingState.load(run_path)
    if state is None:
        # Legacy run — single tier, treat as "complete".
        return jsonify({
            "ok": True,
            "tiered": False,
            "run_dir": str(run_path),
            "message": "legacy single-shot run (no _SAMPLING_STATE.json)",
        })
    return jsonify({
        "ok": True,
        "tiered": True,
        "run_dir": str(run_path),
        "state": state.to_dict(),
        "tier_runs": read_tier_runs(run_path),
    })


@app.route("/api/tier/cancel", methods=["POST"])
def api_tier_cancel():
    """Cancel any in-flight tier for the given parent run_dir.

    Body: {run_dir}
    The orchestrator's per-run cancel event is set; running stages check it
    every iteration and abort cleanly. Partial results are preserved.
    """
    try:
        from fantasyai.aurora.sampling import cancel_run, SamplingState
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    body = request.get_json(silent=True) or {}
    rd = body.get("run_dir")
    if not rd:
        return jsonify({"ok": False, "error": "run_dir required"}), 400
    rp = Path(rd)
    if not rp.exists():
        return jsonify({"ok": False, "error": "run_dir does not exist"}), 404
    cancel_run(rp)
    state = SamplingState.load(rp)
    return jsonify({"ok": True, "run_dir": str(rp),
                    "state": state.to_dict() if state else None,
                    "message": "cancellation flag set; in-flight tiers will stop at the next checkpoint"})


@app.route("/api/tier/run_full", methods=["POST"])
def api_tier_run_full():
    """Kick off Tier-3 (full-data) analysis for a parent run_dir.

    Body: {run_dir}
    Requires Tier-2 to be complete. The full run executes in a background
    thread; poll /api/tier/status to track progress.
    """
    try:
        from fantasyai.aurora.sampling import (
            start_tier_3, SamplingState, read_tier_runs,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    body = request.get_json(silent=True) or {}
    rd = body.get("run_dir")
    if not rd:
        return jsonify({"ok": False, "error": "run_dir required"}), 400
    rp = Path(rd)
    if not rp.exists():
        return jsonify({"ok": False, "error": "run_dir does not exist"}), 404
    state = SamplingState.load(rp)
    if state is None:
        return jsonify({"ok": False, "error": "not a tiered run (no _SAMPLING_STATE.json)"}), 400
    if state.tiers["2"].state != "complete":
        return jsonify({"ok": False,
                        "error": f"Tier 2 must be complete first (currently: {state.tiers['2'].state})"}), 400

    def _runner_for_tier(sample_path, parent_run_dir):
        # Tier 3 = full pipeline at full data. 30-minute timeout.
        result = _trigger_run(
            dataset=str(sample_path), seed=42,
            also_math=True, math_v2=True, deep_math=True,
            also_llm=True, also_motif=True, also_orchestrator=True,
            use_cache=False,
            timeout_seconds=1800,
            tier_label="Tier 3 (full data)",
        )
        if result.get("ok") and result.get("run_dir"):
            return Path(result["run_dir"])
        return None

    try:
        start_tier_3(rp, runner=_runner_for_tier, background=True)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    state = SamplingState.load(rp)
    return jsonify({"ok": True, "run_dir": str(rp),
                    "state": state.to_dict() if state else None,
                    "message": "Tier 3 started in the background; poll /api/tier/status"})


@app.route("/api/scheduling/info")
def api_scheduling_info():
    """Tell the user how to set up the autonomous tick on their OS.

    No daemon — a thin pull-based design that works with whatever scheduler
    the user already has.
    """
    import sys, os, platform
    here = Path(__file__).resolve().parent
    tick_script = here / "bin" / "aurora_tick.py"
    py = sys.executable
    return jsonify({
        "ok": True,
        "tick_endpoint": "/api/agents/tick",
        "tick_script": str(tick_script) if tick_script.exists() else None,
        "python_path": py,
        "platform": platform.system(),
        "examples": {
            "windows_task_scheduler": (
                "schtasks /Create /SC HOURLY /TN AuroraTick /TR "
                f"\"\\\"{py}\\\" \\\"{tick_script}\\\"\""
            ),
            "cron_hourly": (
                f"0 * * * * {py} {tick_script} >> ~/.aurora/tick.log 2>&1"
            ),
            "manual": (
                f"{py} {tick_script}"
            ),
        },
    })


@app.route("/api/templates/<template_id>/evolution_proposals")
def api_template_evolution(template_id: str):
    try:
        from fantasyai.aurora.system_model.evolution import evolution_proposals
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503
    return jsonify({"ok": True,
                    "proposals": evolution_proposals(template_id)})


# ---------------------------------------------------------------------------
# Knowledge Bank API (Brief 4)
# ---------------------------------------------------------------------------

@app.route("/api/knowledge_bank/summary")
def api_kb_summary():
    """Bank-wide totals + per-source / per-domain breakdown."""
    try:
        from fantasyai.aurora.knowledge_bank import get_bank
        bank = get_bank()
        return jsonify({"ok": True, "summary": bank.summary()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/knowledge_bank/entry/<entry_id>")
def api_kb_entry(entry_id: str):
    """Full content of one entry by id."""
    try:
        from fantasyai.aurora.knowledge_bank import get_bank
        bank = get_bank()
        e = bank.get(entry_id)
        if e is None:
            return jsonify({"ok": False, "error": "not_found",
                              "entry_id": entry_id}), 404
        return jsonify({"ok": True, "entry": e.to_dict()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/knowledge_bank/search")
def api_kb_search():
    """Free-text search the knowledge bank.

    Query params:
      q       — query string (required)
      mode    — 'structured' | 'semantic' | 'hybrid' (default hybrid)
      domain  — repeated; restricts to these domains
      limit   — max results, default 20
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "missing_q"}), 400
    mode_str = (request.args.get("mode") or "hybrid").lower()
    domains = request.args.getlist("domain") or None
    try:
        limit = int(request.args.get("limit") or 20)
    except Exception:
        limit = 20
    try:
        from fantasyai.aurora.knowledge_bank import retrieve, RetrievalMode
        mode = {"structured": RetrievalMode.STRUCTURED,
                  "semantic": RetrievalMode.SEMANTIC,
                  "hybrid": RetrievalMode.HYBRID}.get(mode_str,
                                                        RetrievalMode.HYBRID)
        results = retrieve(q, mode=mode, domains=domains, max_results=limit)
        return jsonify({
            "ok": True,
            "n_results": len(results),
            "results": [
                {"id": r.entry.id, "name": r.entry.name,
                  "domain": r.entry.domain, "source": r.entry.source,
                  "definition": (r.entry.definition or "")[:300],
                  "relevance": float(r.relevance),
                  "matched_via": list(r.matched_via)}
                for r in results
            ],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/knowledge_bank/seed_ingest", methods=["POST"])
def api_kb_seed_ingest():
    """Trigger a (re)ingest of the curated seed entries. Idempotent."""
    try:
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        res = run_seed_ingest()
        return jsonify({"ok": True, "result": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


# ---------- Brief 5 admin endpoints ---------------------------------------

# Token for KB admin endpoints. Set AURORA_KB_ADMIN_TOKEN to require it.
# NOTE: this function intentionally has a different name from the
# learning admin _admin_token_ok at line ~2909 — they use different env
# vars and different fallback behaviour. Keep them distinct so neither
# accidentally shadows the other (an earlier bug had the same name and
# the second def silently overrode the first, bypassing AURORA_ADMIN_TOKEN
# for the learning endpoints).
def _kb_admin_token_ok() -> bool:
    expected = os.environ.get("AURORA_KB_ADMIN_TOKEN")
    if not expected:
        # Dev convenience: when no token is set, allow loopback callers only.
        addr = (request.remote_addr or "")
        return addr.startswith(("127.", "::1", "localhost"))
    supplied = (request.headers.get("X-Admin-Token")
                or request.args.get("token") or "")
    return supplied == expected


@app.route("/api/knowledge_bank/preflight")
def api_kb_preflight():
    """Pre-flight report: embedder, GPU, disk, license audit, checkpoints."""
    try:
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        return jsonify({"ok": True, "report": run_preflight()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


# Live ingest-state registry — populated by background ingest threads
# launched via /api/knowledge_bank/admin/ingest.
_KB_INGEST_STATE: Dict[str, Any] = {
    "running": False, "stage": None, "source": None,
    "n_done": 0, "started_at": None,
    "last_progress_at": None, "events": [],
    "result": None,
}


@app.route("/api/knowledge_bank/admin/stages")
def api_kb_admin_stages():
    if not _kb_admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        from fantasyai.aurora.knowledge_bank.ingest.pipeline import (
            INGEST_STAGES,
        )
        return jsonify({"ok": True, "stages": INGEST_STAGES})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/knowledge_bank/admin/ingest/start", methods=["POST"])
def api_kb_admin_ingest_start():
    """Kick off a stage in the background. Idempotent — refuses to start
    a second ingest while one is running (the brief explicitly forbids
    parallel stages)."""
    if not _kb_admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    stage_id = body.get("stage_id") or "stage_1_fred"
    import threading as _t
    if _KB_INGEST_STATE.get("running"):
        return jsonify({"ok": False,
                          "error": "ingest_already_running",
                          "current_stage": _KB_INGEST_STATE.get("stage")}), 409
    def _runner():
        try:
            from fantasyai.aurora.knowledge_bank.ingest.pipeline import run_stage
            _KB_INGEST_STATE["running"] = True
            _KB_INGEST_STATE["stage"] = stage_id
            _KB_INGEST_STATE["started_at"] = time.time()
            _KB_INGEST_STATE["events"] = []
            _KB_INGEST_STATE["result"] = None
            def cb(ev):
                _KB_INGEST_STATE["source"] = ev.get("source")
                _KB_INGEST_STATE["n_done"] = ev.get("n_done") or 0
                _KB_INGEST_STATE["last_progress_at"] = time.time()
                ev_log = _KB_INGEST_STATE["events"]
                ev_log.append({"ts": time.time(), **ev})
                if len(ev_log) > 200:
                    del ev_log[:len(ev_log) - 200]
            res = run_stage(stage_id, progress_cb=cb)
            _KB_INGEST_STATE["result"] = res
        except Exception as e:
            _KB_INGEST_STATE["result"] = {"ok": False, "error": str(e)}
        finally:
            _KB_INGEST_STATE["running"] = False
    _t.Thread(target=_runner, daemon=True,
                name=f"aurora-kb-ingest-{stage_id}").start()
    return jsonify({"ok": True, "stage_id": stage_id, "started": True})


@app.route("/api/knowledge_bank/admin/ingest/status")
def api_kb_admin_ingest_status():
    if not _kb_admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify({"ok": True, "state": _KB_INGEST_STATE})


@app.route("/api/knowledge_bank/admin/quarantine")
def api_kb_admin_quarantine():
    if not _kb_admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    only_unresolved = request.args.get("unresolved", "1") == "1"
    try:
        from fantasyai.aurora.knowledge_bank import get_bank
        bank = get_bank()
        rows = bank.list_quarantine(only_unresolved=only_unresolved,
                                       limit=int(request.args.get("limit", 200)))
        return jsonify({"ok": True, "quarantine": rows,
                         "count": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/knowledge_bank/admin/quarantine/<int:qid>/resolve",
            methods=["POST"])
def api_kb_admin_resolve(qid: int):
    if not _kb_admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    resolution = body.get("resolution") or "rejected"
    try:
        from fantasyai.aurora.knowledge_bank import get_bank
        ok = get_bank().resolve_quarantine(qid, resolution)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/knowledge_bank/admin/license_audit")
def api_kb_admin_license_audit():
    """Per-license breakdown + count of unlicensed entries."""
    try:
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.schema import KNOWN_LICENSES
        bank = get_bank()
        cur = bank._conn.execute(
            """SELECT license, COUNT(*) FROM entries
               WHERE revoked = 0 GROUP BY license""")
        per_license = {row[0] or "<none>": int(row[1])
                         for row in cur.fetchall()}
        unlicensed = per_license.get("", 0) + per_license.get("<none>", 0)
        return jsonify({
            "ok": True,
            "per_license": per_license,
            "n_unlicensed": unlicensed,
            "known_licenses": list(KNOWN_LICENSES.keys()),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@app.route("/api/knowledge_bank/admin/revoke/<entry_id>",
            methods=["POST"])
def api_kb_admin_revoke(entry_id: str):
    """Soft-delete an entry. Audit trail preserved in revoked_reason."""
    if not _kb_admin_token_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    body = request.get_json(force=True, silent=True) or {}
    reason = body.get("reason") or "maintainer revoked"
    try:
        from fantasyai.aurora.knowledge_bank import get_bank
        ok = get_bank().revoke(entry_id, reason)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 503


# ---------- Public KB browser route ---------------------------------------

@app.route("/knowledge_bank")
def page_knowledge_bank_browser():
    """Public-facing knowledge-bank browser. Renders a static HTML page
    that hits /api/knowledge_bank/{summary,search,entry/<id>}."""
    page = Path(__file__).parent / "frontend" / "knowledge_bank.html"
    if not page.exists():
        return ("Knowledge bank browser page not found. "
                "Expected at frontend/knowledge_bank.html"), 404
    return page.read_text(encoding="utf-8")


@app.route("/knowledge_bank/admin/<page>")
def page_kb_admin(page: str):
    """Admin pages — ingest progress / quarantine. Token-gated server-side
    via /api/* endpoints; the page itself is static."""
    allowed = {"ingest", "quarantine"}
    if page not in allowed:
        return "not found", 404
    f = Path(__file__).parent / "frontend" / f"knowledge_bank_admin_{page}.html"
    if not f.exists():
        return ("Admin page not found. Expected at "
                f"frontend/knowledge_bank_admin_{page}.html"), 404
    return f.read_text(encoding="utf-8")


@app.route("/aurora/plot")
def aurora_plot():
    run_dir = request.args.get("run_dir")
    name = request.args.get("name")
    if not run_dir or not name:
        return jsonify({"ok": False, "error": "Missing run_dir or name"}), 400
    try:
        rd = _safe_path(Path(run_dir))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    p = rd / "plots" / name
    if not p.exists():
        abort(404)
    return send_file(str(p), mimetype="image/png")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    # AURORA_HOST defaults to 127.0.0.1 — loopback only, not network-
    # accessible. Override with AURORA_HOST=0.0.0.0 only if you
    # intentionally want LAN access (and ideally pair with
    # AURORA_ADMIN_TOKEN — see SECURITY.md).
    host = os.environ.get("AURORA_HOST", "127.0.0.1")
    port = int(os.environ.get("AURORA_PORT", "8000"))
    debug = bool(int(os.environ.get("AURORA_DEBUG", "0")))
    no_browser = bool(int(os.environ.get("AURORA_NO_BROWSER", "0")))

    print(f"[aurora] frontend = {FRONTEND_DIR}")
    print(f"[aurora] outputs  = {DEFAULT_OUTPUTS}")
    print(f"[aurora] runner   = {RUNNER_MODULE}")
    print(f"[aurora] http://{host}:{port}")

    # First-run: auto-bootstrap the knowledge bank if no DB exists yet.
    # Embeds the 59 curated seed entries into ~/.aurora/knowledge_bank/
    # so synthesis can cite from day one. Adds ~10-20s to the FIRST boot;
    # subsequent boots skip this entirely (the file exists). Runs in a
    # background thread so the server still binds quickly.
    # Skip when AURORA_NO_KB_BOOTSTRAP=1 (CI / scripted envs).
    if not int(os.environ.get("AURORA_NO_KB_BOOTSTRAP", "0") or 0):
        try:
            kb_db = Path.home() / ".aurora" / "knowledge_bank" / "main.db"
            if not kb_db.exists():
                print(f"[aurora] knowledge bank not found at {kb_db}")
                print(f"[aurora] running first-time seed ingest in background...")
                import threading as _kb_threading

                def _bootstrap_kb():
                    try:
                        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
                        res = run_seed_ingest()
                        n = (res or {}).get("entries_ingested") or (res or {}).get("count") or "?"
                        print(f"[aurora] knowledge bank ready: {n} seed entries ingested")
                    except Exception as e:
                        print(f"[aurora] KB bootstrap warning ({type(e).__name__}): {e}")
                        print(f"[aurora] manually: python -m fantasyai.aurora.knowledge_bank.ingest --seed-only")

                _kb_threading.Thread(target=_bootstrap_kb, daemon=True).start()
        except Exception as e:
            print(f"[aurora] KB bootstrap check skipped ({type(e).__name__})")

    # Auto-open the Studio in the user's default browser ~1.2s after
    # the server binds. We skip when:
    #   * AURORA_NO_BROWSER=1   (headless / scripted / CI use)
    #   * AURORA_DEBUG=1        (reloader would spawn a 2nd tab on every
    #                            code change — annoying)
    #   * WERKZEUG_RUN_MAIN     (already inside a Flask reloader child)
    if not no_browser and not debug and not os.environ.get("WERKZEUG_RUN_MAIN"):
        import threading
        import webbrowser
        # Localhost-friendly URL: use 'localhost' instead of the bind
        # host string so wildcard binds (0.0.0.0) still open a working URL.
        url = f"http://localhost:{port}" if host in ("0.0.0.0", "::") else f"http://{host}:{port}"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app.run(host=host, port=port, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    main()

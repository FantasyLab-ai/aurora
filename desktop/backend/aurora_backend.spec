# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Aurora backend (the bundled Flask server +
analysis pipeline that the desktop app spawns).

Build from the REPO ROOT so the relative resource paths resolve:

    cd <repo root>
    pyinstaller desktop/backend/aurora_backend.spec --noconfirm

Produces an onedir bundle at dist/aurora-backend/ containing
aurora-backend[.exe] plus all dependencies. onedir (not onefile) is
deliberate for this scientific stack: faster startup (no per-launch
temp unpack), far fewer antivirus false-positives, and Tauri bundles
the whole folder as an app resource.

The entry point is desktop/backend/aurora_entry.py, which dispatches
on argv to act as the server or as either runner hop (see that file).
"""
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

# The spec is invoked from the repo root, so CWD is the repo root.
# Use ABSOLUTE paths throughout: PyInstaller resolves the entry script
# relative to the SPEC FILE's directory (not CWD), which doubles a relative
# path like desktop/backend/... into desktop/backend/desktop/backend/...
REPO = os.path.abspath(os.getcwd())
ENTRY = os.path.join(REPO, "desktop", "backend", "aurora_entry.py")

# CRITICAL for CI: ensure the repo root is on sys.path so the collect_*
# calls below (which import fantasyai / scripts / studio_api at spec-PARSE
# time) succeed regardless of how PyInstaller was invoked. When run as
# `python -m PyInstaller` the cwd is on sys.path automatically; when run as
# the bare `pyinstaller` console script (which CI does), sys.path[0] is the
# launcher dir, NOT the cwd -- so `import fantasyai` fails and the whole
# build dies identically on every OS. This insert makes both paths work.
if REPO not in sys.path:
    sys.path.insert(0, REPO)

datas = []
binaries = []
hiddenimports = []

# ---- Aurora's own packages: code (submodules) + data (JSON templates,
#      seed corpora, equation-translation tables, system-model templates) ----
for pkg in ("fantasyai", "scripts", "aurora_sdk", "aurora_mcp"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception as e:
        print(f"[spec] collect_submodules({pkg}) warning: {e}")
    try:
        datas += collect_data_files(pkg)
    except Exception as e:
        print(f"[spec] collect_data_files({pkg}) warning: {e}")

# Explicit entry targets the dispatcher imports by string.
hiddenimports += [
    "studio_api",
    "scripts.run_aurora_with_steps_1_4",
    "scripts.run_aurora_dataset_runner",
]

# ---- Heavy scientific stack. collect_all grabs submodules + data +
#      compiled binaries. statsmodels and scipy especially need this. ----
for pkg in ("numpy", "pandas", "scipy", "sklearn", "statsmodels",
            "ruptures", "pyarrow", "matplotlib", "flask", "flask_cors",
            "werkzeug", "openpyxl", "requests", "psutil"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"[spec] collect_all({pkg}) warning: {e}")

# matplotlib: force the headless Agg backend so no GUI toolkit is needed.
hiddenimports += ["matplotlib.backends.backend_agg"]

# ---- Bundled read-only resources the frozen app serves / reads:
#      the legacy Studio frontend (iframed by the desktop shell) and the
#      demo fixtures (/api/demo_datasets). Writable data goes to ~/.aurora
#      at runtime — NOT bundled here. ----
datas += [
    (os.path.join(REPO, "frontend"), "frontend"),
    (os.path.join(REPO, "data", "fixtures"), os.path.join("data", "fixtures")),
]

# De-dup hidden imports (collect_* can overlap).
hiddenimports = sorted(set(hiddenimports))


a = Analysis(
    [ENTRY],
    pathex=[REPO],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI toolkits we don't use (matplotlib runs headless Agg).
        "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "IPython", "jupyter", "notebook",
        # collect_all pulls in the full test suites of the scientific
        # stack (pandas.tests alone is thousands of modules + 100s of MB).
        # Aurora never runs them; prune for a lean, fast build.
        "pandas.tests", "numpy.tests", "scipy.tests",
        "sklearn.tests", "statsmodels.tests", "matplotlib.tests",
        "pytest", "_pytest", "hypothesis", "nose",
        # Heavy ML frameworks Aurora's local-first core never imports
        # (narrative uses local Ollama over HTTP, not in-process models).
        # If a runtime ImportError ever surfaces, drop the offender here.
        "torch", "tensorflow", "transformers", "numba",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="aurora-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX trips antivirus; keep off for a signed app
    console=True,         # backend logs to stdout; Tauri captures/hides it
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="aurora-backend",
)

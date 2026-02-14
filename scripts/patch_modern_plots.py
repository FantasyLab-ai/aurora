from __future__ import annotations
import re
from pathlib import Path

ROOT = Path.cwd()

ENGINE = ROOT / "scripts" / "engine_artifacts.py"
if not ENGINE.exists():
    raise SystemExit(f"engine_artifacts.py not found at: {ENGINE}")

# 1) Ensure a stable style module exists in-package
STYLE_DIR = ROOT / "fantasyai" / "aurora" / "viz"
STYLE_DIR.mkdir(parents=True, exist_ok=True)
STYLE_FILE = STYLE_DIR / "style.py"

if not STYLE_FILE.exists():
    STYLE_FILE.write_text(
        """\
from __future__ import annotations

# Aurora plot styling (matplotlib only; no seaborn)
# Safe: if fonts unavailable, matplotlib falls back gracefully.

def apply_aurora_style(plt=None):
    try:
        import matplotlib as mpl
        import matplotlib.pyplot as _plt
        plt = plt or _plt

        # Global rcParams
        mpl.rcParams.update({
            # Typography
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 14,
            "axes.titleweight": "bold",
            "axes.labelsize": 11,

            # Figure / axes
            "figure.dpi": 160,
            "savefig.dpi": 200,
            "figure.facecolor": "#0B1020",   # deep navy
            "axes.facecolor": "#0E1630",
            "axes.edgecolor": (1,1,1,0.08),

            # Grid
            "axes.grid": True,
            "grid.alpha": 0.18,
            "grid.linestyle": "-",
            "grid.linewidth": 0.8,

            # Ticks / labels
            "xtick.color": (1,1,1,0.75),
            "ytick.color": (1,1,1,0.75),
            "axes.labelcolor": (1,1,1,0.85),
            "text.color": (1,1,1,0.92),

            # Spines
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": True,
            "axes.spines.bottom": True,

            # Legend
            "legend.frameon": False,

            # Save
            "savefig.facecolor": "#0B1020",
            "savefig.bbox": "tight",
        })

        return True
    except Exception:
        return False


def aurora_finalize_axes(ax):
    \"\"\"Small per-axes finishing touches.\"\"\"
    try:
        # Soften remaining spines
        for spine in ax.spines.values():
            spine.set_alpha(0.15)
        ax.title.set_color((1,1,1,0.95))
        ax.xaxis.label.set_color((1,1,1,0.85))
        ax.yaxis.label.set_color((1,1,1,0.85))
    except Exception:
        pass
""",
        encoding="utf-8"
    )

# 2) Patch engine_artifacts.py to apply style inside _try_generate_plots
txt = ENGINE.read_text(encoding="utf-8")

# Backup once
bak = ENGINE.with_suffix(".py.bak_modernplots")
if not bak.exists():
    bak.write_text(txt, encoding="utf-8")

# (A) Add safe import helper near the optional deps
# We'll insert after the matplotlib try/except block if not already present.
if "apply_aurora_style" not in txt:
    # Insert after the matplotlib try/except block
    marker = r"try:\s*\n\s*import matplotlib\.pyplot as plt\s*\nexcept Exception:\s*# pragma: no cover\s*\n\s*plt = None\s*# type: ignore\s*\n"
    m = re.search(marker, txt, flags=re.MULTILINE)
    if not m:
        raise SystemExit("Could not find matplotlib optional import block to patch (unexpected engine_artifacts.py structure).")

    insert = m.group(0) + "\n\n# Optional plot styling (safe)\ntry:\n    from fantasyai.aurora.viz.style import apply_aurora_style, aurora_finalize_axes\nexcept Exception:  # pragma: no cover\n    apply_aurora_style = None  # type: ignore\n    aurora_finalize_axes = None  # type: ignore\n"
    txt = txt.replace(m.group(0), insert)

# (B) Inside _try_generate_plots, apply style right after pyplot import
needle = "import matplotlib.pyplot as plt"
if "apply_aurora_style(plt)" not in txt:
    txt = txt.replace(
        needle,
        needle + "\n\n        # Apply Aurora plot style (never fail)\n        try:\n            if apply_aurora_style is not None:\n                apply_aurora_style(plt)\n        except Exception:\n            pass\n"
    )

# (C) Make figures consistently sized + less default-y
# Replace plain plt.figure() with a consistent modern canvas.
txt = re.sub(r"plt\.figure\(\)", "plt.figure(figsize=(9.2, 4.8))", txt)

# (D) After each plot, finalize axes if helper is available.
# We'll patch a few common places: after labels/titles and before tight_layout.
def add_finalize(block: str) -> str:
    if "aurora_finalize_axes" in block:
        return block
    # Add just before tight_layout if present
    return block.replace(
        "plt.tight_layout()",
        "try:\n                    ax = plt.gca()\n                    if aurora_finalize_axes is not None:\n                        aurora_finalize_axes(ax)\n                except Exception:\n                    pass\n                plt.tight_layout()"
    )

# Apply to the four plot sections by targeting the first tight_layout() after each title.
# This is intentionally “best effort” but stable.
parts = txt.split('plt.title("Forecast")')
if len(parts) == 2:
    parts[1] = add_finalize(parts[1])
    txt = 'plt.title("Forecast")'.join(parts)

parts = txt.split('plt.title("Top anomalies")')
if len(parts) == 2:
    parts[1] = add_finalize(parts[1])
    txt = 'plt.title("Top anomalies")'.join(parts)

parts = txt.split('plt.title("Top correlations")')
if len(parts) == 2:
    parts[1] = add_finalize(parts[1])
    txt = 'plt.title("Top correlations")'.join(parts)

parts = txt.split('plt.title("Top missingness")')
if len(parts) == 2:
    parts[1] = add_finalize(parts[1])
    txt = 'plt.title("Top missingness")'.join(parts)

ENGINE.write_text(txt, encoding="utf-8")

print("✅ Modern plot style installed and wired into scripts/engine_artifacts.py")
print(f"   - style module: {STYLE_FILE}")
print(f"   - backup:       {bak}")

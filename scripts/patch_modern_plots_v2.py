from __future__ import annotations

from pathlib import Path
import re

ROOT = Path.cwd()

ENGINE = ROOT / "scripts" / "engine_artifacts.py"
if not ENGINE.exists():
    raise SystemExit(f"engine_artifacts.py not found at: {ENGINE}")

# -----------------------------
# 1) Ensure viz/style.py exists and exports apply_aurora_style
# -----------------------------
VIZ_DIR = ROOT / "fantasyai" / "aurora" / "viz"
VIZ_DIR.mkdir(parents=True, exist_ok=True)

init_py = VIZ_DIR / "__init__.py"
if not init_py.exists():
    init_py.write_text("# Aurora viz package\n", encoding="utf-8")

STYLE = VIZ_DIR / "style.py"
STYLE.write_text(
"""from __future__ import annotations

# Aurora plot styling (matplotlib only). Safe fallbacks:
# - If matplotlib isn't available, caller import will fail and be caught.
# - If fonts aren't installed, matplotlib will fall back automatically.

def apply_aurora_style(plt=None) -> bool:
    try:
        import matplotlib as mpl
        import matplotlib.pyplot as _plt

        plt = plt or _plt

        # Modern, dark, "product UI" look (Scale-ish), without seaborn.
        mpl.rcParams.update({
            # Typography
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.titlesize": 14,
            "axes.titleweight": "bold",
            "axes.labelsize": 11,

            # Figure
            "figure.dpi": 160,
            "savefig.dpi": 200,
            "figure.facecolor": "#0B1020",
            "axes.facecolor": "#0E1630",
            "axes.edgecolor": (1, 1, 1, 0.10),

            # Grid
            "axes.grid": True,
            "grid.alpha": 0.18,
            "grid.linestyle": "-",
            "grid.linewidth": 0.8,

            # Ticks / text
            "xtick.color": (1, 1, 1, 0.78),
            "ytick.color": (1, 1, 1, 0.78),
            "axes.labelcolor": (1, 1, 1, 0.88),
            "text.color": (1, 1, 1, 0.92),

            # Spines
            "axes.spines.top": False,
            "axes.spines.right": False,

            # Legend
            "legend.frameon": False,

            # Save
            "savefig.facecolor": "#0B1020",
            "savefig.bbox": "tight",
        })

        return True
    except Exception:
        return False


def aurora_finalize_axes(ax) -> None:
    \"\"\"Per-axes finishing touches (best-effort).\"\"\"
    try:
        for spine in ax.spines.values():
            spine.set_alpha(0.18)
        ax.title.set_color((1, 1, 1, 0.95))
        ax.xaxis.label.set_color((1, 1, 1, 0.88))
        ax.yaxis.label.set_color((1, 1, 1, 0.88))
    except Exception:
        pass
""",
    encoding="utf-8",
)

# -----------------------------
# 2) Patch engine_artifacts.py safely (no regex indentation traps)
# -----------------------------
txt = ENGINE.read_text(encoding="utf-8")
bak = ENGINE.with_suffix(".py.bak_modernplots_v2")
if not bak.exists():
    bak.write_text(txt, encoding="utf-8")

lines = txt.splitlines(True)

def find_line_idx(predicate):
    for i, ln in enumerate(lines):
        if predicate(ln):
            return i
    return -1

# 2A) Add safe import block once, right after the matplotlib optional import section
# We anchor on: "plt = None  # type: ignore" from the optional matplotlib import.
if "from fantasyai.aurora.viz.style import apply_aurora_style" not in txt:
    idx = find_line_idx(lambda s: "plt = None" in s and "type: ignore" in s)
    if idx == -1:
        raise SystemExit("Could not find the optional matplotlib import block to anchor style import.")
    insert = (
        "\n"
        "# Optional plot styling (safe)\n"
        "try:\n"
        "    from fantasyai.aurora.viz.style import apply_aurora_style, aurora_finalize_axes\n"
        "except Exception:  # pragma: no cover\n"
        "    apply_aurora_style = None  # type: ignore\n"
        "    aurora_finalize_axes = None  # type: ignore\n"
        "\n"
    )
    lines.insert(idx + 1, insert)

# Refresh combined text
txt2 = "".join(lines)

# 2B) Inside _try_generate_plots: call apply_aurora_style right after pyplot import
# We insert after the exact line: "import matplotlib.pyplot as plt" (the one inside the function)
if "apply_aurora_style(plt)" not in txt2:
    # Find the function
    m = re.search(r"def _try_generate_plots\([^)]*\):", txt2)
    if not m:
        raise SystemExit("Could not find _try_generate_plots() in engine_artifacts.py")

    # Find the pyplot import inside the function (first occurrence after function def)
    start = m.start()
    sub = txt2[start:]
    mi = sub.find("import matplotlib.pyplot as plt")
    if mi == -1:
        raise SystemExit("Could not find 'import matplotlib.pyplot as plt' inside _try_generate_plots().")

    # Determine indentation for the import line
    # The import line should already be indented; we mirror that indentation.
    # Get the line containing the import
    before = sub[:mi]
    line_start = before.rfind("\n") + 1
    import_line = sub[line_start: sub.find("\n", line_start)]
    indent = import_line.split("import")[0]

    inject = (
        "\n"
        f"{indent}# Apply Aurora plot style (never fail)\n"
        f"{indent}try:\n"
        f"{indent}    if apply_aurora_style is not None:\n"
        f"{indent}        apply_aurora_style(plt)\n"
        f"{indent}except Exception:\n"
        f"{indent}    pass\n"
    )

    # Insert inject immediately after the pyplot import line
    # We do this by replacing the first occurrence in the substring after the function start.
    sub2 = sub.replace("import matplotlib.pyplot as plt", "import matplotlib.pyplot as plt" + inject, 1)
    txt2 = txt2[:start] + sub2

# 2C) Add finalize hook just before each plt.tight_layout() (best effort)
# This won’t break anything if not present.
if "aurora_finalize_axes" in txt2 and "plt.tight_layout()" in txt2 and "aurora_finalize_axes(ax)" not in txt2:
    txt2 = txt2.replace(
        "plt.tight_layout()",
        "try:\n            ax = plt.gca()\n            if aurora_finalize_axes is not None:\n                aurora_finalize_axes(ax)\n        except Exception:\n            pass\n        plt.tight_layout()"
    )

ENGINE.write_text(txt2, encoding="utf-8")

print("✅ Patched modern plots v2 (safe, no indentation traps).")
print(f"   - style:  {STYLE}")
print(f"   - backup: {bak}")

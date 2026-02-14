from __future__ import annotations

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
    """Per-axes finishing touches (best-effort)."""
    try:
        for spine in ax.spines.values():
            spine.set_alpha(0.18)
        ax.title.set_color((1, 1, 1, 0.95))
        ax.xaxis.label.set_color((1, 1, 1, 0.88))
        ax.yaxis.label.set_color((1, 1, 1, 0.88))
    except Exception:
        pass

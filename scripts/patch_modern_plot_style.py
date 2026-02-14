from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def ensure_style_module() -> None:
    viz_dir = ROOT / "fantasyai" / "aurora" / "viz"
    viz_dir.mkdir(parents=True, exist_ok=True)

    init_py = viz_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text("", encoding="utf-8")

    style_py = viz_dir / "style.py"

    # If you already made a style.py elsewhere and want to keep it:
    # - either copy it here, or tell me where it is and I'll wire to it.
    if not style_py.exists():
        style_py.write_text(
            """from __future__ import annotations

def apply_style(dark: bool = False, dpi: int = 180) -> None:
    \"\"\"Apply a modern, presentation-ready Matplotlib style globally.\"\"\"
    import matplotlib as mpl

    try:
        mpl.rcdefaults()
    except Exception:
        pass

    bg = "#0B1220" if dark else "#FFFFFF"
    fg = "#E5E7EB" if dark else "#111827"
    grid = "#22304A" if dark else "#E5E7EB"
    spine = "#2B3A55" if dark else "#D1D5DB"

    mpl.rcParams.update({
        "figure.dpi": dpi,
        "savefig.dpi": dpi,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.10,

        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.labelsize": 11,

        "figure.facecolor": bg,
        "axes.facecolor": bg,
        "text.color": fg,
        "axes.labelcolor": fg,
        "axes.titlecolor": fg,
        "xtick.color": fg,
        "ytick.color": fg,

        "axes.grid": True,
        "grid.color": grid,
        "grid.alpha": 0.35 if dark else 0.55,
        "grid.linewidth": 0.8,

        "axes.edgecolor": spine,
        "axes.linewidth": 1.0,

        "xtick.major.size": 0,
        "ytick.major.size": 0,

        "legend.frameon": False,
        "lines.linewidth": 2.2,
        "figure.autolayout": False,
    })
""",
            encoding="utf-8",
        )

def patch_scripts_engine_artifacts() -> None:
    path = ROOT / "scripts" / "engine_artifacts.py"
    if not path.exists():
        raise SystemExit(f"scripts/engine_artifacts.py not found at: {path}")

    s = path.read_text(encoding="utf-8")

    # Already wired?
    if "from fantasyai.aurora.viz.style import apply_style" in s or "apply_style(" in s:
        # But only skip if it looks like our injected block is present
        if "Apply consistent modern styling for all plots" in s:
            print("ℹ️ scripts/engine_artifacts.py already wired to apply_style(); skipping.")
            return

    # We want to inject inside _try_generate_plots near the top:
    # after the dependency guard (plt/pd/np None), or at the function start.

    # First try: after the guard + return block
    guard_pat = r"(def _try_generate_plots\\(.*?\\):\\s*\\n(?:.|\\n)*?\\n\\s*if plt is None or pd is None or np is None:\\s*\\n\\s*return\\s+\\{[^\\}]*\\}\\s*\\n)"
    m = re.search(guard_pat, s, flags=re.MULTILINE)
    injected = (
        "    # Apply consistent modern styling for all plots (safe/no-op if matplotlib config differs)\\n"
        "    try:\\n"
        "        from fantasyai.aurora.viz.style import apply_style\\n"
        "        apply_style(dark=False, dpi=180)\\n"
        "    except Exception:\\n"
        "        pass\\n\\n"
    )

    if m:
        insert_at = m.end(1)
        s2 = s[:insert_at] + injected + s[insert_at:]
        path.write_text(s2, encoding="utf-8")
        print("✅ Patched scripts/engine_artifacts.py to call apply_style() after dependency guard.")
        return

    # Fallback: insert right after function signature line
    sig_pat = r"(def _try_generate_plots\\(.*?\\):\\s*\\n)"
    m2 = re.search(sig_pat, s)
    if not m2:
        raise SystemExit("Could not find _try_generate_plots(...) in scripts/engine_artifacts.py")

    insert_at = m2.end(1)
    s2 = s[:insert_at] + injected + s[insert_at:]
    path.write_text(s2, encoding="utf-8")
    print("✅ Patched scripts/engine_artifacts.py to call apply_style() at start of _try_generate_plots().")

def main() -> None:
    ensure_style_module()
    patch_scripts_engine_artifacts()
    print("✅ Modern plot styling installed + wired (scripts/engine_artifacts.py).")

if __name__ == "__main__":
    main()

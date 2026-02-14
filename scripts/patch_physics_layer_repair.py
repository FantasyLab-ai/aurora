from __future__ import annotations

import re, sys, shutil
from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "fantasyai" / "aurora" / "math" / "deep_math.py"
DISC = ROOT / "fantasyai" / "aurora" / "math" / "physics_discovery.py"

def backup(p: Path) -> Path:
    bak = p.with_suffix(p.suffix + ".bak_physfix")
    i = 0
    while bak.exists():
        i += 1
        bak = p.with_suffix(p.suffix + f".bak_physfix{i}")
    shutil.copy2(p, bak)
    return bak

def ensure_physics_discovery_exports():
    if not DISC.exists():
        print("[WARN] physics_discovery.py not found")
        return

    txt = DISC.read_text(encoding="utf-8", errors="replace")

    if re.search(r"^def\s+physics_discovery\s*\(", txt, flags=re.M):
        print("[OK] physics_discovery() already exported")
        return

    bak = backup(DISC)

    if "discover_physics_models" not in txt:
        DISC.write_text(
            txt.rstrip() +
            "\n\ndef physics_discovery(*args, **kwargs):\n"
            "    raise NotImplementedError('discover_physics_models not found')\n",
            encoding="utf-8",
        )
        print(f"[OK] added stub physics_discovery() (backup: {bak.name})")
        return

    wrapper = (
        "\n\n# Engine-compatible wrapper\n"
        "def physics_discovery(df, time_col=None, y_col=None, target_col=None, **kwargs):\n"
        "    tc = time_col\n"
        "    yc = y_col if y_col is not None else target_col\n"
        "    return discover_physics_models(df, time_col=tc, target_col=yc, **kwargs)\n"
    )

    DISC.write_text(txt.rstrip() + wrapper, encoding="utf-8")
    print(f"[OK] injected physics_discovery() wrapper (backup: {bak.name})")

def patch_deep_math():
    if not DEEP.exists():
        print("ERROR: deep_math.py not found")
        sys.exit(2)

    txt = DEEP.read_text(encoding="utf-8", errors="replace")
    bak = backup(DEEP)

    # Remove broken early physics block (if present)
    txt = re.sub(
        r"\n\s*#\s*PHASE34_PATCH_APPLIED[\s\S]*?\n\s*return\s+out\s*\n",
        "\n",
        txt,
        flags=re.M,
    )

    if "AURORA_PHYSICS_PHASES_2_4" not in txt:
        returns = list(re.finditer(r"^\s*return\s+out\s*$", txt, flags=re.M))
        if not returns:
            print("ERROR: no 'return out' found to anchor physics injection")
            sys.exit(3)

        insert_at = returns[-1].start()

        block = (
            "\n    # ============================================================\n"
            "    # AURORA_PHYSICS_PHASES_2_4\n"
            "    # ============================================================\n\n"
            "    if callable(physics_v2):\n"
            "        try:\n"
            "            out['physics_v2'] = physics_v2(df=df, time_col=tcol, y_col=ycol, seed=seed)\n"
            "        except Exception as e:\n"
            "            out['physics_v2'] = {'note': 'failed', 'error': str(e)}\n"
            "    else:\n"
            "        out['physics_v2'] = {'note': 'skipped'}\n\n"
            "    if callable(physics_discovery):\n"
            "        try:\n"
            "            out['physics_discovery'] = physics_discovery(df=df, time_col=tcol, y_col=ycol, seed=seed)\n"
            "        except Exception as e:\n"
            "            out['physics_discovery'] = {'note': 'failed', 'error': str(e)}\n"
            "    else:\n"
            "        out['physics_discovery'] = {'note': 'skipped'}\n\n"
            "    try:\n"
            "        y = pd.to_numeric(df[ycol], errors='coerce').astype(float)\n"
            "        finite = np.isfinite(y)\n"
            "        y2 = y[finite]\n"
            "        if y2.size < 200:\n"
            "            out['physics_invariants'] = {'note': 'skipped', 'error': 'insufficient_samples'}\n"
            "            out['physics_consistency_score'] = None\n"
            "        else:\n"
            "            frac_finite = float(np.mean(finite))\n"
            "            frac_neg = float(np.mean(y2 < 0))\n"
            "            score = max(0.0, min(1.0, frac_finite * (1.0 - frac_neg)))\n"
            "            out['physics_invariants'] = {'note': 'ok', 'finite_fraction': frac_finite}\n"
            "            out['physics_consistency_score'] = score\n"
            "    except Exception as e:\n"
            "        out['physics_invariants'] = {'note': 'failed', 'error': str(e)}\n"
            "        out['physics_consistency_score'] = None\n\n"
        )

        txt = txt[:insert_at] + block + txt[insert_at:]
        print("[OK] injected physics phases 2–4")

    txt = txt.replace("causal_what_if_linear_robust_v2", "causal_what_if_linear")

    DEEP.write_text(txt, encoding="utf-8")
    py_compile.compile(str(DEEP), doraise=True)
    print(f"[DONE] deep_math.py patched cleanly (backup: {bak.name})")

def main():
    ensure_physics_discovery_exports()
    patch_deep_math()
    py_compile.compile(str(DISC), doraise=True)
    print("[DONE] physics_discovery.py compiles clean")

if __name__ == "__main__":
    main()

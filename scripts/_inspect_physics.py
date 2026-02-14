import re
from pathlib import Path

root = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE\fantasyai\aurora\math")
files = ["physics.py","physics_v2.py","physics_discovery.py","deep_math.py"]

def funcs(txt):
    return re.findall(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", txt, flags=re.M)

def imps(txt):
    out=[]
    for line in txt.splitlines()[:160]:
        s=line.strip()
        if s.startswith("import ") or s.startswith("from "):
            out.append(s)
    return out

for fn in files:
    p = root / fn
    print("\n" + "="*80)
    print(fn, "exists=", p.exists())
    if not p.exists():
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    print("size=", len(txt))
    f = funcs(txt)
    print("defs=", ", ".join(f[:30]))
    print("--- imports(head<=160) ---")
    for x in imps(txt):
        if any(k in x for k in ("physics", "numpy", "pandas", "scipy")):
            print(x)
    print("--- mentions ---")
    keys = ["physics_diagnostics", "physics_diagnostics_v2", "physics_discovery", "_build_time_axis", "_infer_time_col", "target_col", "time_col"]
    for i,line in enumerate(txt.splitlines(),1):
        if any(k in line for k in keys):
            print(f"{i:04d}: {line[:160]}")

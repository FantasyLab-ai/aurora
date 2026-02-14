from pathlib import Path
from ucimlrepo import fetch_ucirepo

OUT = Path("fantasyai/data/external/health_uci_diabetes/raw")
OUT.mkdir(parents=True, exist_ok=True)

ds = fetch_ucirepo(id=296)

X = ds.data.features
y = ds.data.targets

X.to_csv(OUT / "uci_296_features.csv", index=False)
if y is not None:
    y.to_csv(OUT / "uci_296_targets.csv", index=False)

# optional metadata dump
(OUT / "uci_296_metadata.txt").write_text(str(ds.metadata), encoding="utf-8")
(OUT / "uci_296_variables.txt").write_text(str(ds.variables), encoding="utf-8")

print(f"[OK] wrote -> {OUT.resolve()}")

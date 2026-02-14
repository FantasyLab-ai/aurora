from __future__ import annotations

import re
from pathlib import Path

RUNNER = Path(r".\scripts\run_aurora_dataset_runner.py")

INJECT_MARKER = "# =========\n# Utilities\n# ========="

CONTRACT_CODE = r'''

# ============================
# Schema / Structure Contract
# ============================

_NA_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "nil", "unknown", "unk", "?", "-", "--", "n\\a"
}

def _normalize_colname(c: str) -> str:
    c2 = str(c).strip()
    c2 = re.sub(r"\\s+", " ", c2)
    return c2

def _coerce_na_tokens(s: pd.Series) -> pd.Series:
    # normalize common string NA tokens to actual NA
    if not pd.api.types.is_object_dtype(s) and not pd.api.types.is_string_dtype(s):
        return s
    ss = s.astype("string")
    lower = ss.str.strip().str.lower()
    mask = lower.isin(_NA_TOKENS)
    out = ss.mask(mask, pd.NA)
    return out

def _sample_series(s: pd.Series, n: int = 5000, seed: int = 0) -> pd.Series:
    s2 = s.dropna()
    if len(s2) <= n:
        return s2
    return s2.sample(n=n, random_state=seed)

def _infer_datetime_quality(s: pd.Series) -> dict:
    ss = _sample_series(s, n=5000, seed=0)
    if len(ss) == 0:
        return {"parse_rate": 0.0, "example": None}
    try:
        dt = pd.to_datetime(ss, errors="coerce", infer_datetime_format=True)
        rate = float(dt.notna().mean())
        ex = None
        good = dt.dropna()
        if len(good) > 0:
            ex = str(good.iloc[0])
        return {"parse_rate": rate, "example": ex}
    except Exception:
        return {"parse_rate": 0.0, "example": None}

def _infer_col_role(df: pd.DataFrame, c: str) -> dict:
    s0 = df[c]
    s = _coerce_na_tokens(s0)

    role = "unknown"
    hints = []
    name = str(c).lower()

    # Geo heuristic
    if name in ("lat", "latitude") or "latitude" in name:
        role = "geo_lat"
        hints.append("name_hint")
    if name in ("lon", "lng", "longitude") or "longitude" in name:
        role = "geo_lon"
        hints.append("name_hint")

    # Datetime heuristic
    dtq = {"parse_rate": 0.0, "example": None}
    if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
        dtq = _infer_datetime_quality(s)
        if dtq["parse_rate"] >= 0.80:
            role = "datetime"
            hints.append("datetime_parse>=0.80")

    # Numeric
    if role == "unknown":
        if pd.api.types.is_numeric_dtype(s0):
            role = "numeric"
            hints.append("dtype_numeric")
        else:
            # try coerce numeric if object
            try:
                ss = _sample_series(s, n=5000, seed=0)
                num = pd.to_numeric(ss, errors="coerce")
                num_rate = float(num.notna().mean()) if len(num) else 0.0
                if num_rate >= 0.90:
                    role = "numeric"
                    hints.append("numeric_coerce>=0.90")
            except Exception:
                pass

    # ID-like (override numeric/categorical if it looks like an ID)
    # - very high uniqueness ratio
    # - name patterns
    # - monotonic near-unique numeric
    try:
        ss = _sample_series(s, n=20000, seed=0)
        uniq_ratio = float(ss.nunique(dropna=True) / max(1, len(ss))) if len(ss) else 0.0
    except Exception:
        uniq_ratio = 0.0

    name_id_hint = (
        name == "id"
        or name.endswith("_id")
        or "uuid" in name
        or "guid" in name
        or name.endswith("nbr")
        or name.endswith("number")
        or "incident_id" in name
        or "case_id" in name
    )

    mono_hint = False
    try:
        if role == "numeric":
            sn = pd.to_numeric(_sample_series(s, n=20000, seed=0), errors="coerce").dropna()
            if len(sn) > 20:
                mono_hint = bool(sn.is_monotonic_increasing and (sn.nunique() / len(sn) > 0.98))
    except Exception:
        mono_hint = False

    if name_id_hint or uniq_ratio > 0.98 or mono_hint:
        role = "id_like"
        hints.append("id_like")

    # Categorical vs free_text for remaining object-ish
    if role == "unknown":
        # treat low-cardinality as categorical
        try:
            ss = _sample_series(s, n=20000, seed=0)
            nunq = int(ss.nunique(dropna=True)) if len(ss) else 0
            # free-text tends to have very high cardinality and long strings
            avg_len = float(ss.astype("string").str.len().dropna().mean()) if len(ss) else 0.0
            if nunq <= 200 and (nunq / max(1, len(ss))) < 0.20:
                role = "categorical"
                hints.append("low_cardinality")
            elif avg_len >= 40 and (nunq / max(1, len(ss))) > 0.50:
                role = "free_text"
                hints.append("long_strings_high_cardinality")
            else:
                role = "categorical"
                hints.append("default_categorical")
        except Exception:
            role = "categorical"
            hints.append("fallback_categorical")

    # Datetime quality if not already computed
    if role != "datetime":
        if any(k in name for k in ("time", "date", "timestamp", "datetime", "dt")):
            if dtq["parse_rate"] == 0.0:
                dtq = _infer_datetime_quality(s)

    return {
        "name": _normalize_colname(c),
        "role": role,
        "hints": hints,
        "dtype": str(s0.dtype),
        "missing_rate": float(s.isna().mean()) if len(s) else 0.0,
        "unique_est": int(_sample_series(s, n=20000, seed=0).nunique(dropna=True)) if len(s) else 0,
        "unique_ratio_est": uniq_ratio,
        "datetime_parse_rate": float(dtq.get("parse_rate", 0.0)),
        "datetime_example": dtq.get("example", None),
    }

def _pick_best_time_column(col_profiles: list[dict]) -> str | None:
    # choose the best datetime candidate
    cands = [p for p in col_profiles if p["role"] == "datetime"]
    if not cands:
        # sometimes a date column parses at 0.79 etc; allow "datetime_parse_rate" rescue
        cands = [p for p in col_profiles if p.get("datetime_parse_rate", 0.0) >= 0.80]
    if not cands:
        return None
    cands.sort(key=lambda p: (p.get("datetime_parse_rate", 0.0), -p.get("missing_rate", 0.0)), reverse=True)
    return cands[0]["name"]

def _detect_geo(col_profiles: list[dict]) -> dict:
    lat = next((p["name"] for p in col_profiles if p["role"] == "geo_lat"), None)
    lon = next((p["name"] for p in col_profiles if p["role"] == "geo_lon"), None)
    return {"has_geo": bool(lat and lon), "lat_col": lat, "lon_col": lon}

def _recommend_targets(df: pd.DataFrame, col_profiles: list[dict], time_col: str | None) -> list[dict]:
    # Rank numeric columns that are not id_like and not geo
    bad_roles = {"id_like", "geo_lat", "geo_lon"}
    nums = [p for p in col_profiles if p["role"] == "numeric" and p["role"] not in bad_roles]
    # If the dataset includes common “count” fields, boost them
    boosts = []
    for p in nums:
        name = p["name"].lower()
        score = 0.0
        score += (1.0 - p["missing_rate"]) * 0.5
        score += max(0.0, 0.5 - abs(p["unique_ratio_est"] - 0.2))  # prefer neither constant nor near-unique
        if any(k in name for k in ("count", "num", "number", "total", "injur", "fatal", "killed", "amount", "value")):
            score += 0.5
            boosts.append(p["name"])
        if time_col and any(k in name for k in ("per_day", "rate", "rolling", "avg", "mean")):
            score += 0.2
        p2 = dict(p)
        p2["target_score"] = float(score)
        nums[nums.index(p)] = p2

    nums.sort(key=lambda x: x.get("target_score", 0.0), reverse=True)
    out = []
    for p in nums[:15]:
        out.append({
            "col": p["name"],
            "score": p["target_score"],
            "why": p["hints"],
        })
    return out

def _recommend_views(col_profiles: list[dict], time_col: str | None, geo: dict) -> list[dict]:
    # Views tell downstream modules *how* to interpret the dataset
    roles = {}
    for p in col_profiles:
        roles.setdefault(p["role"], []).append(p["name"])

    views = []
    views.append({"view": "raw_table", "purpose": "Full dataset with type-normalized columns."})
    views.append({"view": "numeric_matrix", "purpose": "Numeric-only columns for correlations/anomalies/optimization."})

    if time_col:
        views.append({"view": "time_series_counts", "purpose": "Aggregate row counts by time (events over time).", "time_col": time_col})
        views.append({"view": "time_series_numeric_sums", "purpose": "Aggregate numeric sums by time for forecasting/regimes.", "time_col": time_col})

    if geo.get("has_geo"):
        views.append({"view": "geo_view", "purpose": "Geospatial sanity + clustering/heatmap features.", "lat_col": geo["lat_col"], "lon_col": geo["lon_col"]})

    # if there are categoricals, recommend grouped time series
    if time_col and roles.get("categorical"):
        views.append({
            "view": "grouped_time_series_topk",
            "purpose": "Top-K categories grouped time series for regime/anomaly segmentation.",
            "time_col": time_col,
            "group_cols": roles["categorical"][:5],
            "topk": 10,
        })

    return views

def build_structure_contract(df: pd.DataFrame, dataset_key: str | None = None) -> dict:
    # profile columns (use sample for speed)
    col_profiles = []
    for c in df.columns:
        try:
            col_profiles.append(_infer_col_role(df, c))
        except Exception as e:
            col_profiles.append({
                "name": _normalize_colname(c),
                "role": "unknown",
                "hints": ["profile_error"],
                "dtype": str(df[c].dtype),
                "missing_rate": float(df[c].isna().mean()) if len(df) else 0.0,
                "unique_est": None,
                "unique_ratio_est": None,
                "error": f"{type(e).__name__}:{e}",
            })

    time_col = _pick_best_time_column(col_profiles)
    geo = _detect_geo(col_profiles)

    # high-level dataset flags
    rows, cols = int(df.shape[0]), int(df.shape[1])
    numeric_cols = [p["name"] for p in col_profiles if p["role"] == "numeric"]
    id_cols = [p["name"] for p in col_profiles if p["role"] == "id_like"]

    eligible = {
        "math_eligible": bool(rows >= 5 and cols >= 2),
        "deep_math_eligible": bool(rows >= 20 and len(numeric_cols) >= 1),
        "time_series_eligible": bool(time_col is not None),
        "geo_eligible": bool(geo.get("has_geo")),
    }

    reason = None
    if cols < 2:
        reason = "Too few columns (likely not a tabular dataset)."
    elif rows < 5:
        reason = "Too few rows for stable statistics."
    elif len(numeric_cols) < 1:
        reason = "No numeric columns detected."

    targets = _recommend_targets(df, col_profiles, time_col)
    views = _recommend_views(col_profiles, time_col, geo)

    return {
        "version": "structure_contract_v1",
        "dataset_key": dataset_key,
        "shape": {"rows": rows, "cols": cols},
        "eligible": eligible,
        "reason_if_ineligible": reason,
        "columns": col_profiles,
        "time": {"best_time_col": time_col},
        "geo": geo,
        "id_like_cols": id_cols[:200],
        "numeric_cols": numeric_cols[:200],
        "recommended_targets": targets,
        "recommended_views": views,
    }
'''

def main():
    txt = RUNNER.read_text(encoding="utf-8")

    if "structure_contract_v1" in txt and "build_structure_contract" in txt:
        print("Patch already applied (structure contract present).")
        return

    # 1) Inject contract code after Utilities marker section
    idx = txt.find(INJECT_MARKER)
    if idx == -1:
        raise RuntimeError("Could not find Utilities marker to inject after.")
    # inject right after the Utilities header block (after marker line)
    inject_at = idx + len(INJECT_MARKER)
    txt = txt[:inject_at] + CONTRACT_CODE + txt[inject_at:]

    # 2) In _main, after df loaded, write structure_contract.json
    # Find the line: df = _load_df(resolved["table"])
    m = re.search(r"\n\s*df\s*=\s*_load_df\(resolved\[\s*[\"']table[\"']\s*\]\)\s*\n", txt)
    if not m:
        raise RuntimeError("Could not find df = _load_df(resolved['table']) line.")

    insert = (
        "\n"
        "    # Build rich schema/structure contract\n"
        "    contract = build_structure_contract(df, dataset_key=resolved.get('dataset_key'))\n"
        "    _write_json(run_dir / \"structure_contract.json\", contract)\n"
        "\n"
        "    # Keep legacy structure.json for backward compatibility\n"
        "    structure = _assess_dataset_structure(df)\n"
        "    _write_json(run_dir / \"structure.json\", structure)\n"
        "\n"
    )

    # Remove/replace the old structure write block if present
    # We'll replace the existing structure assignment + write later in file
    # by inserting contract block immediately after df load, then removing the later structure write if it exists.
    txt = txt[:m.end()] + insert + txt[m.end():]

    # 3) Remove the old lines that write structure.json (to avoid duplicate/confusion)
    txt = re.sub(
        r"\n\s*structure\s*=\s*_assess_dataset_structure\(df\)\s*\n\s*_write_json\(run_dir\s*/\s*\"structure\.json\",\s*structure\)\s*\n",
        "\n",
        txt,
        count=1
    )

    RUNNER.write_text(txt, encoding="utf-8")
    print("✅ Patched run_aurora_dataset_runner.py: added structure_contract.json (schema contract)")

if __name__ == "__main__":
    main()

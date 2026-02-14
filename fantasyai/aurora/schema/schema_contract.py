import numpy as np
import pandas as pd
from typing import Dict, Any

def build_schema_contract(df: pd.DataFrame, seed: int = 42) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    datetime_cols = []

    for c in df.columns:
        if c in numeric_cols:
            continue
        try:
            parsed = pd.to_datetime(df[c], errors="coerce")
            if parsed.notna().mean() > 0.5:
                datetime_cols.append(c)
        except Exception:
            pass

    df_numeric = df[numeric_cols].copy() if numeric_cols else None
    df_time = None

    if datetime_cols:
        tcol = datetime_cols[0]
        df_time = df.copy()
        df_time[tcol] = pd.to_datetime(df_time[tcol], errors="coerce")
        df_time = df_time.sort_values(tcol)

    recommended_targets = []
    if df_numeric is not None:
        variances = df_numeric.var().sort_values(ascending=False)
        recommended_targets = variances.index.tolist()

    contract = {
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
        "numeric_col_count": len(numeric_cols),
        "numeric_cols": numeric_cols,
        "datetime_cols": datetime_cols,
        "recommended_targets": recommended_targets[:5],
        "has_time_axis": bool(datetime_cols),
        "seed": seed,
    }

    return {
        "contract": contract,
        "df_canonical": df,
        "df_numeric_view": df_numeric,
        "df_time_view": df_time,
        "df_deep_math_view": df_numeric,
    }

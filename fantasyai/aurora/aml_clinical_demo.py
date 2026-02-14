import os
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

try:
    # Prefer the shared DATA_ROOT if available
    from fantasyai.aurora.public_data_adapter import DATA_ROOT
except ImportError:
    # Fallback if import path ever changes
    DATA_ROOT = os.path.join(os.path.dirname(__file__), "..", "data")
    DATA_ROOT = os.path.abspath(DATA_ROOT)


def _load_aml_clinical():
    path = os.path.join(DATA_ROOT, "aml_clinical_clean.csv")
    if not os.path.exists(path):
        return None, {
            "error": f"AML clinical file not found at {path}. "
                     f"Ensure aml_clinical_clean.csv exists in fantasyai/data."
        }
    df = pd.read_csv(path)
    return df, {"path": path, "shape": df.shape, "columns": df.columns.tolist()}


def _select_numeric(df: pd.DataFrame) -> pd.DataFrame:
    num_df = df.select_dtypes(include=["number"]).copy()
    # Drop columns that are almost constant (no variance)
    nunique = num_df.nunique()
    keep_cols = nunique[nunique > 1].index.tolist()
    return num_df[keep_cols]


def run_aml_clinical_demo():
    """
    Run a basic Aurora-style analysis on AML clinical data:
      - numeric feature summary
      - correlation matrix
      - PCA
      - simple anomaly flags via z-score
    """
    df, meta = _load_aml_clinical()
    if df is None:
        return {"meta": meta, "note": "AML clinical data not available."}

    num_df = _select_numeric(df)
    if num_df.empty:
        return {
            "meta": meta,
            "note": "No numeric columns found in AML clinical table.",
        }

    # Basic stats
    stats = num_df.describe().to_dict()

    # Correlation matrix
    corr = num_df.corr().round(3).to_dict()

    # PCA (2 components)
    scaler = StandardScaler()
    X = scaler.fit_transform(num_df.values)
    pca = PCA(n_components=min(2, X.shape[1]))
    X_pca = pca.fit_transform(X)

    explained_var = pca.explained_variance_ratio_.tolist()

    # Simple anomaly score: mean absolute z-score across features
    z = np.abs((X - X.mean(axis=0)) / (X.std(axis=0) + 1e-8))
    anomaly_score = z.mean(axis=1)
    threshold = float(np.percentile(anomaly_score, 95))
    anomaly_flags = (anomaly_score > threshold).tolist()

    summary = {
        "meta": {
            **meta,
            "num_numeric_features": num_df.shape[1],
            "numeric_features": num_df.columns.tolist(),
        },
        "stats": stats,
        "correlation": corr,
        "pca": {
            "explained_variance_ratio": explained_var,
        },
        "anomalies": {
            "threshold_95pct": threshold,
            "num_flagged": int(sum(anomaly_flags)),
            "fraction_flagged": float(sum(anomaly_flags) / len(anomaly_flags)),
        },
    }

    return summary

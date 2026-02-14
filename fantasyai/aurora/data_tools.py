# fantasyai/aurora/data_tools.py
import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_regression

def simple_impute(df, cols):
    for c in cols:
        df[c] = df[c].fillna(df[c].median())
    return df

def feature_importance(df, target_col, candidate_cols):
    X = df[candidate_cols].values
    y = df[target_col].values
    mi = mutual_info_regression(X, y, discrete_features=False)
    return sorted(zip(candidate_cols, mi), key=lambda x: -x[1])

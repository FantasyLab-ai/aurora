"""I/O helpers for Aurora — file-format-agnostic loading.

Goal: the runner should not care whether the user dropped in a CSV, JSON, JSONL,
Parquet, or Excel file. Auto-detect format, load to a DataFrame, return it
along with provenance metadata for honest downstream reporting.
"""
from .loader import load_dataset, LoadResult, LoadError, supported_formats

__all__ = ["load_dataset", "LoadResult", "LoadError", "supported_formats"]

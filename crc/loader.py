"""
loader.py — File ingestion utilities.

Supports ODS, XLSX, XLS, CSV for source and KPI files.
Dimensions file must be XLSX or CSV with columns: Dimension, Value.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Generic DataFrame loader
# ---------------------------------------------------------------------------

def load_dataframe(content: bytes, filename: str) -> pd.DataFrame:
    """Load file bytes into a DataFrame. Multi-sheet files return the first sheet."""
    suffix = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if suffix == ".ods":
            result = pd.read_excel(tmp_path, engine="odf", sheet_name=None)
        elif suffix in (".xlsx", ".xls"):
            result = pd.read_excel(tmp_path, sheet_name=None)
        elif suffix == ".csv":
            return pd.read_csv(tmp_path)
        else:
            raise ValueError(f"Unsupported file type: '{suffix}'. Use ODS, XLSX, XLS or CSV.")
    finally:
        os.unlink(tmp_path)

    if isinstance(result, dict):
        if not result:
            raise ValueError("File contains no sheets.")
        return list(result.values())[0]
    return result


# ---------------------------------------------------------------------------
# Typed loaders with validation
# ---------------------------------------------------------------------------

def load_source(content: bytes, filename: str) -> pd.DataFrame:
    """Load source data file. Returns DataFrame; validates it is non-empty."""
    df = load_dataframe(content, filename)
    if df.empty:
        raise ValueError("Source file is empty.")
    return df


def load_kpis(content: bytes, filename: str) -> pd.DataFrame:
    """
    Load KPI reference file.

    Required columns : Code, Name, Schema
    Optional columns : Type, filter_json, aggregation, agg_column
    """
    df = load_dataframe(content, filename)
    required = {"Code", "Name", "Schema"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"KPI file is missing required columns: {sorted(missing)}. "
            "Required: Code, Name, Schema."
        )
    df["Code"] = df["Code"].astype(str).str.strip()
    df["Name"] = df["Name"].astype(str).str.strip()
    # Strip trailing newlines/spaces from Name
    df["Name"] = df["Name"].str.rstrip("\n").str.strip()
    return df


def load_dimensions(content: bytes, filename: str) -> pd.DataFrame:
    """
    Load dimensions reference file.

    Required columns : Dimension, Value
    """
    df = load_dataframe(content, filename)
    required = {"Dimension", "Value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Dimensions file is missing required columns: {sorted(missing)}. "
            "Required: Dimension, Value."
        )
    df = df.dropna(subset=["Dimension", "Value"]).copy()
    df["Dimension"] = df["Dimension"].astype(str).str.strip()
    df["Value"] = df["Value"].astype(str).str.strip()
    return df

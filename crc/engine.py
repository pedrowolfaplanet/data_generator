"""
engine.py - KPI computation engine.

Supports an arbitrary number of dynamic dimension placeholders in the
dimension-combination pattern. For each placeholder the engine:

  1. Fuzzy-matches every unique source-column value to a canonical Dimension value.
  2. Builds a reverse index: dim_val -> {source_vals that map to it}.
  3. Takes the Cartesian product of all canonical Dimension values across
     every placeholder (so every platform entity appears even with value = 0).
  4. For each KPI x combination cell:
       a. Intersects source rows that satisfy every placeholder constraint.
       b. Applies per-KPI row filters (filter_json).
       c. Aggregates (count / mean / sum).

Pattern examples
----------------
  "Bergé|Spain|{Sociedad}"         -> 1 placeholder -> N rows per KPI
  "Grupo presidente|{Empresa}"     -> 1 placeholder -> M rows per KPI
  "{Grupo}|{Country}|{Sociedad}"   -> 3 placeholders -> NxMxP rows per KPI
  "ACME"                           -> 0 placeholders -> 1 row per KPI
"""
from __future__ import annotations

import json
import re
from itertools import product
from typing import Any

import pandas as pd

from src.matcher import DimensionMatcher, normalize


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_placeholders(pattern: str) -> list[str]:
    return re.findall(r"\{([^}]+)\}", pattern)


def _get_unit(schema_str: Any) -> str:
    try:
        obj = json.loads(str(schema_str))
        units = obj.get("units", [])
        return str(units[0]) if units else "integer"
    except Exception:
        return "integer"


def _parse_filters(val: Any) -> dict[str, str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return {}
    raw = str(val).strip()
    if raw in ("", "nan", "NaN", "None", "none"):
        return {}
    try:
        return {str(k): str(v) for k, v in json.loads(raw).items()}
    except json.JSONDecodeError:
        return {}


def _apply_filters(df: pd.DataFrame, filters: dict[str, str]) -> pd.DataFrame:
    """Case/accent-insensitive equality filter."""
    if not filters:
        return df
    mask = pd.Series(True, index=df.index)
    for col, val in filters.items():
        if col not in df.columns:
            continue
        mask &= df[col].astype(str).apply(normalize) == normalize(val)
    return df[mask]


def _safe_str(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "count"
    return str(val).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute(
    source_df: pd.DataFrame,
    kpi_df: pd.DataFrame,
    matcher: DimensionMatcher,
    dim_pattern: str,
    placeholder_col_map: dict,
    fixed: dict,
) -> tuple:
    """
    Compute KPI values and return (output_df, match_log_df).

    Parameters
    ----------
    source_df           : Raw source data.
    kpi_df              : KPI reference (Code, Name, Schema; optionally
                          filter_json, aggregation, agg_column, Type).
    matcher             : Pre-built DimensionMatcher (threshold already set).
    dim_pattern         : Dimension-combination pattern,
                          e.g. "Grupo presidente|{Empresa}"
    placeholder_col_map : {placeholder_name: source_column_name}
                          Only entries for placeholders in the Dimensions
                          catalogue are used.
    fixed               : Dict of fixed output-field values.

    Returns
    -------
    (output_df, match_log_df)
    """
    all_phs = _get_placeholders(dim_pattern)
    # Only keep placeholders that exist in the Dimensions catalogue
    valid_phs = [p for p in all_phs if p in matcher.dimension_names()]

    # --- 1. Per-placeholder: match all unique source values ---------------
    # fwd_matches : {ph: {src: (dim_val, method, score)}}
    # rev_indexes : {ph: {dim_val: {src_vals}}}
    fwd_matches = {}
    rev_indexes = {}

    for ph in valid_phs:
        src_col = placeholder_col_map.get(ph)
        if not src_col or src_col not in source_df.columns:
            fwd_matches[ph] = {}
            rev_indexes[ph] = {}
            continue
        unique_src = source_df[src_col].dropna().astype(str).unique().tolist()
        fwd = matcher.match_all(unique_src, ph)
        fwd_matches[ph] = fwd
        rev_indexes[ph] = matcher.build_reverse_index(unique_src, ph)

    # --- 2. Match log (one entry per unique source value per placeholder) --
    log_rows = []
    for ph in valid_phs:
        src_col = placeholder_col_map.get(ph, "")
        for src, (dim_val, method, score) in fwd_matches[ph].items():
            log_rows.append({
                "placeholder": ph,
                "source_column": src_col,
                "original": src,
                "normalizado": normalize(src),
                "corrigido": dim_val,
                "metodo": method,
                "score": round(score / 100, 6),
            })
    match_log_df = pd.DataFrame(log_rows)

    # --- 3. Cartesian product of all canonical Dimension values -----------
    if valid_phs:
        dim_value_lists = [matcher.values(ph) for ph in valid_phs]
        all_combos = list(product(*dim_value_lists))
    else:
        # No dynamic placeholders -> single row per KPI (total)
        all_combos = [()]

    # --- 4. Compute KPIs --------------------------------------------------
    output_rows = []

    for _, kpi in kpi_df.iterrows():
        kpi_name = str(kpi["Name"]).strip()
        kpi_code = str(kpi["Code"]).strip()
        kpi_type = (
            str(kpi["Type"]).strip()
            if "Type" in kpi.index and pd.notna(kpi.get("Type"))
            else "NUMBER"
        )
        unit = _get_unit(kpi.get("Schema", "{}"))
        filters = _parse_filters(kpi.get("filter_json"))
        aggregation = _safe_str(kpi.get("aggregation", "count")).lower()
        agg_col = _safe_str(kpi.get("agg_column", ""))

        for combo in all_combos:

            # a. Intersect source rows that satisfy every placeholder
            if valid_phs:
                mask = pd.Series(True, index=source_df.index)
                for ph, dim_val in zip(valid_phs, combo):
                    src_col = placeholder_col_map.get(ph)
                    if not src_col:
                        continue
                    src_vals = rev_indexes[ph].get(dim_val, set())
                    mask &= source_df[src_col].astype(str).isin(src_vals)
                rows_for_combo = source_df[mask]
            else:
                rows_for_combo = source_df

            # b. Apply KPI filters
            filtered = _apply_filters(rows_for_combo, filters)

            # c. Aggregate
            if aggregation == "count":
                value = float(len(filtered))
            elif aggregation in ("mean", "avg"):
                if agg_col and agg_col in filtered.columns:
                    v = filtered[agg_col].mean()
                    value = 0.0 if pd.isna(v) else float(v)
                else:
                    value = float(len(filtered))
            elif aggregation == "sum":
                if agg_col and agg_col in filtered.columns:
                    value = float(filtered[agg_col].sum())
                else:
                    value = float(len(filtered))
            else:
                value = float(len(filtered))

            # d. Build combination string
            dim_str = dim_pattern
            for ph, dim_val in zip(valid_phs, combo):
                dim_str = dim_str.replace("{" + ph + "}", dim_val)

            output_rows.append({
                "KPI Name": kpi_name,
                "KPI Code": kpi_code,
                "KPI Type": kpi_type,
                "Periodicity": fixed.get("Periodicity", "YEAR"),
                "Periodicity Value": fixed.get("Periodicity Value", ""),
                "Periodicity Year": fixed.get("Periodicity Year", ""),
                "Dimension Combination": dim_str,
                "Status": fixed.get("Status", "EMPTY"),
                "Categories": fixed.get("Categories", ""),
                "Fillers": fixed.get("Fillers", ""),
                "Validators": fixed.get("Validators", ""),
                "Fill data due date": fixed.get("Fill data due date", ""),
                "Validate due date": fixed.get("Validate due date", ""),
                "Value": round(value, 4),
                "Unit": unit,
                "Comment": "",
                "Filler": fixed.get("Filler", ""),
                "Validator": fixed.get("Validator", ""),
                "Cell": "",
            })

    output_df = pd.DataFrame(output_rows)
    return output_df, match_log_df

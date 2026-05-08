"""
matcher.py — Fuzzy dimension matching.

Maps raw source values (e.g. "BERGE LOGISTICA SL") to canonical
Dimension values (e.g. "BELO_Bergé Logística SL") using a cascade of:
  1. Exact match
  2. Accent / case-normalised exact match
  3. Token-sort-ratio fuzzy match (original strings)
  4. Token-sort-ratio fuzzy match (normalised strings)
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

import pandas as pd
from rapidfuzz import fuzz, process


# ---------------------------------------------------------------------------
# String normalisation
# ---------------------------------------------------------------------------

def normalize(s: str) -> str:
    """Uppercase, strip accents, collapse non-word chars to single space."""
    s = str(s).strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# DimensionMatcher
# ---------------------------------------------------------------------------

class DimensionMatcher:
    """Fuzzy-matches raw source values against Dimension reference values."""

    def __init__(self, dimensions_df: pd.DataFrame, threshold: int = 80) -> None:
        self.threshold = threshold
        self._groups: dict[str, list[str]] = {}
        self._norm_to_raw: dict[str, dict[str, str]] = {}

        for dim, group in dimensions_df.groupby("Dimension"):
            values = list(group["Value"].dropna().astype(str).unique())
            key = str(dim)
            self._groups[key] = values
            self._norm_to_raw[key] = {normalize(v): v for v in values}

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def dimension_names(self) -> list[str]:
        return list(self._groups.keys())

    def values(self, dim: str) -> list[str]:
        return self._groups.get(dim, [])

    # ------------------------------------------------------------------
    # Single-value match
    # ------------------------------------------------------------------

    def match(self, source: str, dim: str) -> tuple[str, str, float]:
        """
        Match *source* against the catalogue for *dim*.

        Returns
        -------
        (matched_value, method, score_0_to_100)
        """
        candidates = self._groups.get(dim, [])
        if not candidates:
            return source, "no_dimension", 0.0

        # 1. Exact
        if source in candidates:
            return source, "exact", 100.0

        # 2. Normalised exact
        src_norm = normalize(source)
        n2r = self._norm_to_raw.get(dim, {})
        if src_norm in n2r:
            return n2r[src_norm], "normalized_exact", 100.0

        # 3. Fuzzy on original strings
        res = process.extractOne(source, candidates, scorer=fuzz.token_sort_ratio)
        if res and res[1] >= self.threshold:
            return res[0], "fuzzy", float(res[1])

        # 4. Fuzzy on normalised strings
        res_n = process.extractOne(
            src_norm, list(n2r.keys()), scorer=fuzz.token_sort_ratio
        )
        if res_n and res_n[1] >= self.threshold:
            return n2r[res_n[0]], "fuzzy_normalized", float(res_n[1])

        return source, "unmatched", 0.0

    # ------------------------------------------------------------------
    # Batch match
    # ------------------------------------------------------------------

    def match_all(
        self, source_values: list[str], dim: str
    ) -> dict[str, tuple[str, str, float]]:
        """Match every unique value in *source_values* against *dim*.

        Returns ``{source_val: (matched_val, method, score)}``.
        """
        return {v: self.match(v, dim) for v in source_values}

    # ------------------------------------------------------------------
    # Reverse index: dim_val → set of source values
    # ------------------------------------------------------------------

    def build_reverse_index(
        self, source_values: list[str], dim: str
    ) -> dict[str, set[str]]:
        """Return ``{dim_val: {source_val, ...}}`` for all source values."""
        fwd = self.match_all(source_values, dim)
        rev: dict[str, set[str]] = defaultdict(set)
        for src, (dim_val, _, _) in fwd.items():
            rev[dim_val].add(src)
        return dict(rev)

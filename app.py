"""
KPI Upload Generator — Streamlit App
=====================================
Upload Dados Fonte + KPI Reference + Dimensions to generate a standardised
KPI data-upload file ready for your SaaS platform.

Supports any number of dynamic placeholders in the dimension-combination
pattern.  Each ``{Placeholder}`` is resolved independently against its own
source column, and the output is the Cartesian product of all matched values.
"""
from __future__ import annotations

import re
from itertools import product as cartesian_product
from math import prod

import pandas as pd
import streamlit as st

from src.engine import _get_placeholders, compute
from src.exporter import to_excel_bytes
from src.loader import load_dimensions, load_kpis, load_source
from src.matcher import DimensionMatcher

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="KPI Upload Generator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        [data-testid="stSidebar"] { min-width: 320px; }
        .block-container { padding-top: 1.5rem; }
        h1 { margin-bottom: 0.2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────
st.title("📊 KPI Upload Generator")
st.caption(
    "Upload source data, KPI definitions, and a dimensions reference to generate "
    "a standardised KPI upload file for your reporting platform."
)

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — file uploads
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📁 Input Files")

    fonte_file = st.file_uploader(
        "Dados Fonte",
        type=["ods", "xlsx", "csv"],
        help="Raw employee / entity records (ODS, XLSX or CSV).",
    )
    kpi_file = st.file_uploader(
        "KPI Reference",
        type=["csv", "xlsx", "ods"],
        help="KPI definitions — optionally includes filter_json and aggregation.",
    )
    dims_file = st.file_uploader(
        "Dimensions",
        type=["xlsx", "csv"],
        help="Dimension catalogue: columns Dimension and Value.",
    )

    st.divider()
    with st.expander("ℹ️ KPI file format"):
        st.markdown(
            """
| Column | Required | Description |
|---|:---:|---|
| `Code` | ✅ | KPI identifier |
| `Name` | ✅ | Display name |
| `Schema` | ✅ | `{"type":"number","units":["integer"]}` |
| `Type` | — | Defaults to `NUMBER` |
| `filter_json` | — | `{"Sexo":"Mujer","Clasificacion":"DIRECTIVO"}` |
| `aggregation` | — | `count` *(default)*, `mean`, `sum` |
| `agg_column` | — | Column for `mean`/`sum`, e.g. `SBA` |
            """
        )
    with st.expander("ℹ️ Dimension pattern syntax"):
        st.markdown(
            """
Use `{DimensionName}` for dynamic values; literal text stays as-is.

| Pattern | Example output |
|---|---|
| `{Empresa}` | `HOTEL PIC COZUMEL` |
| `Grupo presidente\|{Empresa}` | `Grupo presidente\|HOTEL PIC COZUMEL` |
| `Bergé\|Spain\|{Sociedad}` | `Bergé\|Spain\|BMAR_Bergé Marítima SL` |
| `{Grupo}\|{Empresa}` | `Grupo Presidente\|HOTEL PIC COZUMEL` |
            """
        )

# ─────────────────────────────────────────────────────────────────────────────
# Guard — require all three files
# ─────────────────────────────────────────────────────────────────────────────
if not all([fonte_file, kpi_file, dims_file]):
    st.info("👈 Upload all three files in the sidebar to get started.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# Load files (cached per content hash)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner="Loading files…")
def _load(
    fonte_b: bytes, fonte_n: str,
    kpi_b: bytes,   kpi_n: str,
    dims_b: bytes,  dims_n: str,
):
    source_df = load_source(fonte_b, fonte_n)
    kpi_df    = load_kpis(kpi_b, kpi_n)
    dims_df   = load_dimensions(dims_b, dims_n)
    return source_df, kpi_df, dims_df


try:
    source_df, kpi_df, dims_df = _load(
        fonte_file.read(), fonte_file.name,
        kpi_file.read(),   kpi_file.name,
        dims_file.read(),  dims_file.name,
    )
except Exception as exc:
    st.error(f"**Error loading files:** {exc}")
    st.stop()

_base_matcher = DimensionMatcher(dims_df)
dim_names     = _base_matcher.dimension_names()
source_cols   = list(source_df.columns)

# ─────────────────────────────────────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────────────────────────────────────
tab_config, tab_preview, tab_generate = st.tabs(
    ["⚙️ Configuration", "🔍 Data Preview", "▶️ Generate"]
)

# =============================================================================
# TAB 1 — Configuration
# =============================================================================
with tab_config:
    left, right = st.columns(2, gap="large")

    # ── Dimension combination ─────────────────────────────────────────────
    with left:
        st.subheader("Dimension Combination")

        default_pattern = "|".join(f"{{{d}}}" for d in dim_names)

        dim_pattern = st.text_input(
            "Pattern",
            value=st.session_state.get("dim_pattern", default_pattern),
            key="dim_pattern",
            help=(
                "Literal text stays as-is. Use {DimensionName} for dynamic values.\n\n"
                "Examples:\n"
                "  • Grupo presidente|{Empresa}\n"
                "  • Bergé|Spain|{Sociedad}\n"
                "  • {Grupo}|{Country}|{Sociedad}"
            ),
        )

        all_phs   = _get_placeholders(dim_pattern)
        valid_phs = [p for p in all_phs if p in dim_names]
        bad_phs   = [p for p in all_phs if p not in dim_names]

        if bad_phs:
            st.warning(
                f"Unknown placeholder(s): **{bad_phs}**. "
                f"Available dimensions: `{'`, `'.join(dim_names)}`"
            )

        # ── Per-placeholder column mapping ────────────────────────────────
        placeholder_col_map: dict[str, str] = {}

        if valid_phs:
            st.markdown("**Map each placeholder → source column**")
            for ph in valid_phs:
                n_vals = len(_base_matcher.values(ph))
                placeholder_col_map[ph] = st.selectbox(
                    f"`{{{ph}}}` ({n_vals} dimension values) → source column:",
                    source_cols,
                    key=f"ph_map_{ph}",
                )
        else:
            if not all_phs:
                st.info(
                    "No dynamic placeholders — output will have one row per KPI "
                    "with value = total count across all source rows."
                )

        # ── Estimated output size ─────────────────────────────────────────
        if valid_phs:
            n_combos = prod(len(_base_matcher.values(ph)) for ph in valid_phs)
            n_kpis   = len(kpi_df)
            breakdown = " × ".join(
                f"{len(_base_matcher.values(ph))} {ph}" for ph in valid_phs
            )
            st.info(
                f"**{n_kpis} KPIs** × **{breakdown}** "
                f"= **{n_kpis * n_combos:,} output rows**"
            )

        # ── Fuzzy-match threshold ─────────────────────────────────────────
        fuzzy_threshold = st.slider(
            "Fuzzy-match threshold (%)",
            min_value=50, max_value=100, value=80,
            help="Minimum similarity to accept a fuzzy match (50 = permissive, 100 = exact only).",
        )

    # ── Fixed output fields ───────────────────────────────────────────────
    with right:
        st.subheader("Fixed Output Fields")

        c1, c2 = st.columns(2)
        with c1:
            periodicity = st.selectbox("Periodicity", ["YEAR", "QUARTER", "MONTH", "WEEK", "DAY"])
            per_value   = st.text_input("Periodicity Value", value="2025")
            per_year    = st.text_input("Periodicity Year",  value="2025")
            status      = st.text_input("Status", value="EMPTY")
        with c2:
            categories = st.text_input("Categories", value="")
            fillers    = st.text_input("Fillers",    value="")
            validators = st.text_input("Validators", value="")
            filler     = st.text_input("Filler",     value="")
            validator  = st.text_input("Validator",  value="")

        fill_due     = st.date_input("Fill data due date", value=None)
        validate_due = st.date_input("Validate due date",  value=None)

# Collect fixed fields for the other tabs
fixed_fields = {
    "Periodicity":        periodicity,
    "Periodicity Value":  per_value,
    "Periodicity Year":   per_year,
    "Status":             status,
    "Categories":         categories,
    "Fillers":            fillers,
    "Validators":         validators,
    "Filler":             filler,
    "Validator":          validator,
    "Fill data due date": str(fill_due)     if fill_due     else "",
    "Validate due date":  str(validate_due) if validate_due else "",
}

# =============================================================================
# TAB 2 — Data Preview
# =============================================================================
with tab_preview:
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown(f"**Dados Fonte** — {source_df.shape[0]:,} rows × {source_df.shape[1]} cols")
        st.dataframe(source_df.head(100), use_container_width=True, height=320)

    with c2:
        st.markdown(f"**KPI Reference** — {len(kpi_df)} KPIs")
        preview_cols = [c for c in ["Code", "Name", "filter_json", "aggregation"] if c in kpi_df.columns]
        st.dataframe(kpi_df[preview_cols].head(100), use_container_width=True, height=320)

    with c3:
        st.markdown(
            f"**Dimensions** — {len(dims_df)} values across "
            f"{dims_df['Dimension'].nunique()} dimensions"
        )
        st.dataframe(dims_df.head(100), use_container_width=True, height=320)

    # Show unique values for each mapped source column
    if valid_phs:
        st.divider()
        st.markdown("**Unique values in mapped source columns**")
        cols = st.columns(len(valid_phs))
        for col_ui, ph in zip(cols, valid_phs):
            src_col = placeholder_col_map.get(ph)
            if src_col:
                vals = source_df[src_col].dropna().astype(str).unique()
                with col_ui:
                    st.markdown(f"`{src_col}` → `{{{ph}}}` ({len(vals)} unique)")
                    st.dataframe(
                        pd.DataFrame(sorted(vals), columns=[src_col]),
                        use_container_width=True,
                        height=200,
                    )

# =============================================================================
# TAB 3 — Generate
# =============================================================================
with tab_generate:
    st.subheader("Generate Output File")

    if "filter_json" not in kpi_df.columns:
        st.warning(
            "⚠️ KPI file has no `filter_json` column — all KPIs will be counted "
            "without any row-level filters. Add a `filter_json` column to filter "
            "source rows per KPI."
        )

    # Summary
    if valid_phs:
        n_combos = prod(len(_base_matcher.values(ph)) for ph in valid_phs)
    else:
        n_combos = 1
    n_kpis = len(kpi_df)

    st.info(
        f"Ready to generate **{n_kpis:,} KPIs × {n_combos:,} dimension combinations "
        f"= {n_kpis * n_combos:,} rows**."
    )

    if st.button("▶️ Generate", type="primary", use_container_width=True):
        matcher = DimensionMatcher(dims_df, threshold=fuzzy_threshold)

        with st.spinner(f"Computing {n_kpis} KPIs × {n_combos} combinations…"):
            output_df, log_df = compute(
                source_df=source_df,
                kpi_df=kpi_df,
                matcher=matcher,
                dim_pattern=dim_pattern,
                placeholder_col_map=placeholder_col_map,
                fixed=fixed_fields,
            )

        st.success(
            f"✅ Generated **{len(output_df):,} rows** "
            f"across **{output_df['KPI Code'].nunique()} KPIs**."
        )

        st.dataframe(output_df.head(200), use_container_width=True, height=400)

        # Match log
        if not log_df.empty:
            with st.expander(f"🔍 Match log ({len(log_df)} entries)"):
                st.dataframe(log_df, use_container_width=True)

                unmatched = log_df[log_df["metodo"] == "unmatched"]
                if not unmatched.empty:
                    st.warning(
                        f"⚠️ **{len(unmatched)} unmatched** source values — kept as-is. "
                        "Try lowering the fuzzy-match threshold or review the Dimensions file."
                    )
                    st.dataframe(unmatched, use_container_width=True)
                else:
                    st.success("All source values matched a Dimension entry.")

        # Download
        st.divider()
        xlsx = to_excel_bytes(output_df, log_df)
        st.download_button(
            label="⬇️ Download Dados_preenchidos.xlsx",
            data=xlsx,
            file_name="Dados_preenchidos.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )

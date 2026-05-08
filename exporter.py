"""
exporter.py — Excel output builder.

Produces a two-sheet workbook:
  • Data          — the KPI upload rows
  • Log mapeamento — fuzzy-match audit trail
"""
from __future__ import annotations

import io

import pandas as pd


def to_excel_bytes(
    output_df: pd.DataFrame,
    log_df: pd.DataFrame | None = None,
) -> bytes:
    """Serialise *output_df* (and optional *log_df*) to an in-memory XLSX."""
    buf = io.BytesIO()

    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        output_df.to_excel(writer, sheet_name="Data", index=False)

        if log_df is not None and not log_df.empty:
            log_df.to_excel(writer, sheet_name="Log mapeamento", index=False)

        wb = writer.book

        # Header format
        header_fmt = wb.add_format(
            {"bold": True, "bg_color": "#D9E1F2", "border": 1}
        )

        for sheet_name, df in [("Data", output_df), ("Log mapeamento", log_df)]:
            if df is None or df.empty:
                continue
            ws = writer.sheets[sheet_name]

            # Auto-fit column widths and re-apply header format
            for col_idx, col_name in enumerate(df.columns):
                max_content = df[col_name].astype(str).map(len).max()
                col_width = min(max(max_content, len(str(col_name))) + 2, 60)
                ws.set_column(col_idx, col_idx, col_width)
                ws.write(0, col_idx, col_name, header_fmt)

            # Freeze top row
            ws.freeze_panes(1, 0)

    buf.seek(0)
    return buf.read()

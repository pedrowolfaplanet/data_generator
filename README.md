# KPI Upload Generator

A Streamlit app that transforms raw source data into a standardised KPI upload file for your reporting SaaS platform.

## How it works

You provide three inputs:

| Input | What it is |
|---|---|
| **Dados Fonte** | Raw records (employees, transactions, etc.) |
| **KPI Reference** | Definitions of each KPI — including which rows to count and how to aggregate |
| **Dimensions** | Canonical dimension values as they must appear in the platform |

The app fuzzy-matches source entity names → Dimension values (handling uppercase / accent mismatches), applies per-KPI filters, aggregates, and outputs a ready-to-upload Excel file.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/your-org/data-upload-generator.git
cd data-upload-generator

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
streamlit run app.py
```

---

## Input file formats

### Dados Fonte (ODS / XLSX / CSV)
Any tabular file. At minimum it needs one column that identifies the entity
(e.g. `Nombre de la Empresa`) and the columns referenced by your KPI filters.

### KPI Reference (CSV / XLSX)

| Column | Required | Description |
|---|:---:|---|
| `Code` | ✅ | Unique KPI identifier |
| `Name` | ✅ | Display name shown in the output |
| `Schema` | ✅ | JSON — e.g. `{"type":"number","units":["integer"]}` |
| `Type` | — | Defaults to `NUMBER` |
| `filter_json` | — | JSON object of column→value filters applied to source rows before aggregation. Case and accent-insensitive. |
| `aggregation` | — | `count` *(default)*, `mean` or `sum` |
| `agg_column` | — | Source column used for `mean`/`sum` (e.g. `SBA`) |

**Example `filter_json` values:**

```json
{"Sexo": "Mujer", "Clasificacion": "DIRECTIVO"}
{"Contrato Duracion": "Indefinido", "Contrato jornada": "Tiempo Completo"}
{"Categoria Edad": "<30"}
```

See `examples/KPI_template.csv` for a complete example.

### Dimensions (XLSX / CSV)

Two columns:

| Column | Description |
|---|---|
| `Dimension` | Dimension name, e.g. `Grupo`, `Country`, `Sociedad` |
| `Value` | Canonical value for that dimension, exactly as it should appear in the platform |

---

## Dimension Combination pattern

The app lets you define how the dimension combination string is assembled.
Use `{DimensionName}` as a placeholder for values that come from your source data.
Literal text stays as-is.

| Pattern | Result example |
|---|---|
| `{Grupo}\|{Country}\|{Sociedad}` | `Bergé\|Spain\|BMAR_Bergé Marítima SL` |
| `Bergé\|Spain\|{Sociedad}` | `Bergé\|Spain\|BMAR_Bergé Marítima SL` |
| `Bergé\|{Country}\|{Sociedad}` | `Bergé\|France\|FBMA_Bergé Marítima Francia` |

Each `{Placeholder}` is fuzzy-matched against the corresponding dimension in the
Dimensions file. You control the match threshold (50–100 %) in the UI.

---

## Output

The generated `Dados_preenchidos.xlsx` has two sheets:

- **Data** — one row per *(KPI × Dimension value)*, with all fixed fields filled in
- **Log mapeamento** — audit trail of every fuzzy match (original → canonical, method, score)

---

## Project structure

```
.
├── app.py                  # Streamlit application
├── requirements.txt
├── .gitignore
├── README.md
├── src/
│   ├── loader.py           # File ingestion (ODS / XLSX / CSV)
│   ├── matcher.py          # Fuzzy dimension matching
│   ├── engine.py           # KPI computation
│   └── exporter.py         # Excel output builder
└── examples/
    └── KPI_template.csv    # Example KPI file with filter_json and aggregation columns
```

---

## Deploying to Streamlit Cloud

1. Push this repository to GitHub (make sure data files are in `.gitignore`).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Select your repo, branch `main`, and entry point `app.py`.
4. Click **Deploy**.

No secrets or environment variables are required.

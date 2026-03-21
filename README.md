# LookML Glossary Generator

A Python agent that parses LookML model files and generates a structured glossary of business terms including metrics, KPIs, dimensions, table names, and dashboard links.

## Features

- Parses `.model.lkml` files and all included views/dashboards
- Extracts **metrics** (sum, count, average, etc.) and **KPIs** (tagged measures)
- Includes **table names**, **view names**, and **explore context**
- Captures **dashboard links** and **recommended links** from LookML `links` blocks
- Outputs glossary in **JSON**, **Markdown**, or **HTML** (with search)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Command Line

```bash
# Generate Markdown glossary (default)
python -m lookml_glossary examples/ecommerce.model.lkml

# Generate JSON
python -m lookml_glossary examples/ecommerce.model.lkml -f json

# Generate HTML with search
python -m lookml_glossary examples/ecommerce.model.lkml -f html -o glossary.html

# Include additional directories for LookML files
python -m lookml_glossary model.lkml -I ./views -I ./dashboards
```

### As a Library

```python
from lookml_glossary.parser import parse_lookml_model
from lookml_glossary.generator import generate_json, generate_markdown

terms = parse_lookml_model("path/to/model.model.lkml")

# Each term has: name, description, term_type, table_name,
# dashboard_links, recommended_links, is_metric, is_kpi, etc.
for term in terms:
    if term.is_kpi:
        print(f"KPI: {term.name} - {term.description}")
        print(f"  Table: {term.table_name}")
        for link in term.recommended_links:
            print(f"  Dashboard: {link.title} -> {link.url}")
```

## Glossary Term Format

Each glossary entry contains:

| Field | Description |
|-------|-------------|
| `term_name` | Human-readable name of the term |
| `description` | Business description from LookML |
| `type` | One of: explore, view, dimension, metric, kpi, measure |
| `table_name` | Underlying database table |
| `view_name` | LookML view name |
| `is_metric` | Whether this is a metric (aggregated measure) |
| `is_kpi` | Whether this is tagged as a KPI |
| `dashboard_links` | Links to dashboards using this field |
| `recommended_links` | Links defined in the LookML `links` block |
| `sql_expression` | The SQL definition |
| `measure_type` | Type of measure (sum, count, average, etc.) |

## KPI Detection

Measures are classified as KPIs when they have any of these tags:
- `kpi`
- `key_metric`
- `key-metric`
- `key_performance_indicator`

## Running Tests

```bash
pip install pytest
pytest tests/
```

## Example Output

Running against the included example model:

```bash
python -m lookml_glossary examples/ecommerce.model.lkml -f json | python -m json.tool
```

Produces a glossary with explores, views, dimensions, metrics, and KPIs - each annotated with table names, descriptions, and dashboard links.

# LookML Glossary Generator

A Python agent that parses LookML model files and generates a structured glossary of business terms including measures, dimensions, table names, and dashboard links — enriched with synonym detection, related terms, and source table resolution.

## Features

- Parses `.model.lkml` files and all included views/dashboards
- Extracts **measures** (sum, count, average, etc.) and **dimensions**
- Includes **table names**, **view names**, and **explore context** embedded in each term's description
- Captures **dashboard links** and **recommended links** from LookML `link` blocks
- **Synonym detection** — finds fields with identical or near-identical labels across explores
- **Related terms** — identifies complementary fields in the same explore via view co-location and label similarity
- **Source table resolution** — resolves the physical database table backing each field by parsing view files (`sql_table_name`, `derived_table`, implicit naming)
- Outputs glossary in **JSON**, **CSV**, **Markdown**, **HTML**, or **interactive Webapp** (with model diagram, search, filters, and CSV download)

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

# Generate CSV
python -m lookml_glossary examples/ecommerce.model.lkml -f csv -o glossary.csv

# Generate interactive webapp with model diagram
python -m lookml_glossary examples/ecommerce.model.lkml -f webapp -o glossary.html

# Include additional directories for LookML files
python -m lookml_glossary model.lkml -I ./views -I ./dashboards
```

### As a Library

```python
from lookml_glossary.parser import parse_lookml_model
from lookml_glossary.generator import generate_json, generate_webapp

terms = parse_lookml_model("path/to/model.model.lkml")

for term in terms:
    print(f"{term.term_type}: {term.name} - {term.description}")
    if term.synonyms:
        print(f"  Synonyms: {[s['term_name'] for s in term.synonyms]}")
    if term.related_terms:
        print(f"  Related: {[r['term_name'] for r in term.related_terms]}")
    if term.related_entries:
        print(f"  Source: {[r['name'] for r in term.related_entries]}")
```

## Glossary Term Format

Each glossary entry contains:

| Field | Description |
|-------|-------------|
| `term_name` | Human-readable name of the term |
| `description` | Business description enriched with view and explore context |
| `type` | `dimension` or `measure` |
| `table_name` | Underlying database table from the view |
| `view_name` | LookML view name |
| `explore_name` | LookML explore name |
| `model_name` | LookML model name |
| `measure_type` | Type of measure (sum, count, average, etc.) |
| `sql_expression` | The SQL definition |
| `value_format` | Display format |
| `tags` | LookML tags |
| `dashboard_links` | Links to dashboards using this field |
| `recommended_links` | Links defined in the LookML `link` block |
| `synonyms` | Fields with identical/near-identical names across explores |
| `related_terms` | Complementary fields in the same explore (max 5) |
| `related_entries` | Resolved source table(s) for the field |

## Enrichment Details

### Synonym Detection

Fields across all explores are compared for:
- **Identical or near-identical labels** — using token-based similarity (e.g., "Revenue" matches "Total Revenue")
- **Same underlying view and SQL column** — fields backed by the same data

### Related Terms

For each field, up to 5 related terms are identified from the same explore based on:
- **View co-location** — fields in the same view are considered related
- **Label semantic similarity** — complementary concepts scored by word overlap

### Source Table Resolution

Each field's source table is resolved by parsing LookML view files, handling four patterns in priority order:

1. **`sql_table_name`** — explicit physical table reference
2. **`derived_table` with `sql`** — extracts FROM/JOIN table references from SQL-based PDTs
3. **`derived_table` with `explore_source`** — native derived tables
4. **Implicit** — view name used as table name when no explicit source is defined

Supports `manifest.lkml` constants, templated `${schema}` references, recursive view reference resolution (depth limit 3), and file/view block caching.

## Output Formats

| Format | Flag | Description |
|--------|------|-------------|
| Markdown | `-f markdown` | Grouped by type with full metadata (default) |
| JSON | `-f json` | Machine-readable with summary stats |
| HTML | `-f html` | Searchable page with term cards |
| CSV | `-f csv` | Flat table for spreadsheet import |
| Webapp | `-f webapp` | Interactive page with model diagram, search, filters, and CSV download |

## Running Tests

```bash
pip install pytest
pytest tests/
```

## Example Output

```bash
python -m lookml_glossary examples/ecommerce.model.lkml -f json | python -m json.tool
```

Produces a glossary with dimensions and measures — each annotated with table names, descriptions, dashboard links, synonyms, related terms, and source tables.

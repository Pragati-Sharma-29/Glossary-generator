"""Tests for the LookML glossary parser."""

import os
import json
import io

import pytest

from lookml_glossary.parser import (
    parse_lookml_model,
    extract_terms_from_view,
    parse_lookml_file,
    GlossaryTerm,
)
from lookml_glossary.generator import generate_csv, generate_json, generate_markdown, generate_webapp


EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")
MODEL_PATH = os.path.join(EXAMPLES_DIR, "ecommerce.model.lkml")


class TestParseModel:
    def test_parses_example_model(self):
        terms = parse_lookml_model(MODEL_PATH)
        assert len(terms) > 0

    def test_no_explore_or_view_terms(self):
        terms = parse_lookml_model(MODEL_PATH)
        explores = [t for t in terms if t.term_type == "explore"]
        views = [t for t in terms if t.term_type == "view"]
        assert len(explores) == 0
        assert len(views) == 0

    def test_terms_contain_view_and_explore_context(self):
        terms = parse_lookml_model(MODEL_PATH)
        # Every term should have view context in description
        for t in terms:
            assert "View:" in t.description
        # Terms linked to an explore should have explore context
        explore_terms = [t for t in terms if t.explore_name]
        assert len(explore_terms) > 0
        for t in explore_terms:
            assert "Explore:" in t.description

    def test_contains_metrics(self):
        terms = parse_lookml_model(MODEL_PATH)
        metrics = [t for t in terms if t.is_metric]
        assert len(metrics) >= 4

    def test_contains_kpis(self):
        terms = parse_lookml_model(MODEL_PATH)
        kpis = [t for t in terms if t.is_kpi]
        assert len(kpis) >= 3

    def test_kpi_has_dashboard_links(self):
        terms = parse_lookml_model(MODEL_PATH)
        revenue = [t for t in terms if t.name == "Total Revenue"]
        assert len(revenue) == 1
        assert len(revenue[0].recommended_links) >= 1

    def test_table_names_populated(self):
        terms = parse_lookml_model(MODEL_PATH)
        # Check that terms with a table_name have it populated
        terms_with_table = [t for t in terms if t.table_name is not None]
        assert len(terms_with_table) > 0

    def test_dimensions_extracted(self):
        terms = parse_lookml_model(MODEL_PATH)
        dims = [t for t in terms if t.term_type == "dimension"]
        assert len(dims) >= 5


class TestGenerateJson:
    def test_json_output(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_json(terms, buf)
        data = json.loads(buf.getvalue())
        assert "glossary" in data
        assert "summary" in data
        assert data["summary"]["total_terms"] == len(terms)

    def test_json_kpi_entries(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_json(terms, buf)
        data = json.loads(buf.getvalue())
        kpis = [e for e in data["glossary"] if e.get("is_kpi")]
        assert len(kpis) >= 3


class TestGenerateMarkdown:
    def test_markdown_output(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_markdown(terms, buf)
        md = buf.getvalue()
        assert "# LookML Glossary" in md
        assert "KPI" in md
        assert "Metric" in md


class TestGenerateCsv:
    def test_csv_has_header_and_rows(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_csv(terms, buf)
        lines = buf.getvalue().strip().split("\n")
        assert lines[0].startswith("term_name,")
        assert len(lines) == len(terms) + 1  # header + data rows

    def test_csv_contains_kpi_flag(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_csv(terms, buf)
        content = buf.getvalue()
        assert "Yes" in content  # is_metric or is_kpi column

    def test_csv_contains_table_names(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_csv(terms, buf)
        content = buf.getvalue()
        assert "public.orders" in content
        assert "public.users" in content

    def test_csv_contains_recommended_links(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_csv(terms, buf)
        content = buf.getvalue()
        assert "Revenue Dashboard" in content


class TestGenerateWebapp:
    def test_webapp_output(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_webapp(terms, buf)
        html = buf.getvalue()
        assert "LookML Glossary" in html
        assert "Model Diagram" in html
        assert "Download CSV" in html

    def test_webapp_contains_diagram_elements(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_webapp(terms, buf)
        html = buf.getvalue()
        assert "ecommerce" in html
        assert "Orders" in html
        assert "public.orders" in html

    def test_webapp_has_csv_download_script(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_webapp(terms, buf)
        html = buf.getvalue()
        assert "lookml_glossary.csv" in html


class TestSingleView:
    def test_extract_from_view_dict(self):
        parsed = parse_lookml_file(os.path.join(EXAMPLES_DIR, "orders.view.lkml"))
        view = parsed["views"][0]
        terms = extract_terms_from_view(view, model_name="test")
        assert any(t.name == "Total Revenue" for t in terms)
        assert any(t.is_kpi for t in terms)
        assert not any(t.term_type == "view" for t in terms)

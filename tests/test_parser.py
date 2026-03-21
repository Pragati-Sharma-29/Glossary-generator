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
from lookml_glossary.generator import generate_json, generate_markdown


EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")
MODEL_PATH = os.path.join(EXAMPLES_DIR, "ecommerce.model.lkml")


class TestParseModel:
    def test_parses_example_model(self):
        terms = parse_lookml_model(MODEL_PATH)
        assert len(terms) > 0

    def test_contains_explores(self):
        terms = parse_lookml_model(MODEL_PATH)
        explores = [t for t in terms if t.term_type == "explore"]
        assert len(explores) >= 2
        names = {t.name for t in explores}
        assert "Orders" in names
        assert "Products" in names

    def test_contains_views(self):
        terms = parse_lookml_model(MODEL_PATH)
        views = [t for t in terms if t.term_type == "view"]
        assert len(views) >= 3

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
        views = [t for t in terms if t.term_type == "view"]
        for v in views:
            assert v.table_name is not None

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


class TestSingleView:
    def test_extract_from_view_dict(self):
        parsed = parse_lookml_file(os.path.join(EXAMPLES_DIR, "orders.view.lkml"))
        view = parsed["views"][0]
        terms = extract_terms_from_view(view, model_name="test")
        assert any(t.name == "Total Revenue" for t in terms)
        assert any(t.is_kpi for t in terms)
        assert any(t.term_type == "view" for t in terms)

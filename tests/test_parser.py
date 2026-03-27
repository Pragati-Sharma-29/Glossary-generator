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
        assert "## Measures" in md
        assert "## Dimensions" in md


class TestGenerateCsv:
    def test_csv_has_header_and_rows(self):
        import csv
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_csv(terms, buf)
        buf.seek(0)
        reader = csv.reader(buf)
        rows = list(reader)
        assert rows[0][0] == "term_name"
        assert len(rows) == len(terms) + 1  # header + data rows

    def test_csv_contains_measure_type(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_csv(terms, buf)
        content = buf.getvalue()
        assert "measure" in content  # type column contains measure

    def test_csv_contains_table_names(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_csv(terms, buf)
        content = buf.getvalue()
        assert "public.orders" in content
        assert "analytics_v2.dim_users" in content

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


class TestDimensionGroupExpansion:
    def test_timeframes_expanded(self):
        terms = parse_lookml_model(MODEL_PATH)
        # orders.created has timeframes: [raw, date, week, month, quarter, year]
        tf_ids = {t.field_id for t in terms if t.field_id.startswith("orders.created_")}
        assert "orders.created_date" in tf_ids
        assert "orders.created_week" in tf_ids
        assert "orders.created_month" in tf_ids
        assert "orders.created_year" in tf_ids

    def test_expanded_terms_have_timeframe_aspect(self):
        terms = parse_lookml_model(MODEL_PATH)
        created_date = next(t for t in terms if t.field_id == "orders.created_date")
        aspect_keys = {a["key"] for a in created_date.aspects}
        assert "timeframe" in aspect_keys
        assert "dimension_group" in aspect_keys
        tf_aspect = next(a for a in created_date.aspects if a["key"] == "timeframe")
        assert tf_aspect["value"] == "date"

    def test_base_group_name_not_emitted(self):
        terms = parse_lookml_model(MODEL_PATH)
        # The base name "created" should not appear when timeframes are specified
        assert not any(t.field_id == "orders.created" for t in terms)


class TestAspects:
    def test_aspects_in_json_output(self):
        terms = parse_lookml_model(MODEL_PATH)
        buf = io.StringIO()
        generate_json(terms, buf)
        data = json.loads(buf.getvalue())
        entries_with_aspects = [e for e in data["glossary"] if e.get("aspects")]
        assert len(entries_with_aspects) > 0

    def test_group_label_captured(self):
        """group_label should appear as an aspect on fields that have it."""
        # Use a synthetic view with group_label
        view = {
            "name": "test_view",
            "dimensions": [{
                "name": "city",
                "sql": "${TABLE}.city",
                "group_label": "Address",
            }],
        }
        terms = extract_terms_from_view(view)
        assert len(terms) == 1
        gl = [a for a in terms[0].aspects if a["key"] == "group_label"]
        assert len(gl) == 1
        assert gl[0]["value"] == "Address"


class TestHiddenFields:
    def test_hidden_flag_set(self):
        view = {
            "name": "v",
            "dimensions": [
                {"name": "visible_field", "sql": "${TABLE}.a"},
                {"name": "hidden_field", "sql": "${TABLE}.b", "hidden": "yes"},
            ],
        }
        terms = extract_terms_from_view(view)
        visible = next(t for t in terms if t.field_id == "v.visible_field")
        hidden = next(t for t in terms if t.field_id == "v.hidden_field")
        assert not visible.is_hidden
        assert hidden.is_hidden

    def test_exclude_hidden_filters(self):
        from lookml_glossary.cli import _filter_hidden
        t1 = GlossaryTerm(name="A", description="", term_type="dimension", field_id="v.a")
        t2 = GlossaryTerm(name="B", description="", term_type="dimension", field_id="v.b", is_hidden=True)
        result = _filter_hidden([t1, t2])
        assert len(result) == 1
        assert result[0].field_id == "v.a"


class TestParameters:
    def test_parameter_parsed(self):
        view = {
            "name": "v",
            "dimensions": [],
            "parameters": [{
                "name": "date_granularity",
                "type": "unquoted",
                "default_value": "day",
                "allowed_values": [
                    {"label": "Day", "value": "day"},
                    {"label": "Week", "value": "week"},
                ],
            }],
        }
        terms = extract_terms_from_view(view)
        params = [t for t in terms if t.term_type == "parameter"]
        assert len(params) == 1
        assert params[0].field_id == "v.date_granularity"
        aspect_keys = {a["key"] for a in params[0].aspects}
        assert "parameter_type" in aspect_keys
        assert "default_value" in aspect_keys
        assert "allowed_values" in aspect_keys


class TestDashboardLookmlExtension:
    def test_lookml_extension_matches(self):
        from lookml_glossary.parser import _matches_include
        # A .dashboard.lookml file should match a *.dashboard.lkml pattern
        assert _matches_include(
            "/proj/dashboards/my.dashboard.lookml",
            "dashboards/*.dashboard.lookml",
            "/proj",
        )
        # Cross-extension: .lkml pattern should also match .lookml files
        assert _matches_include(
            "/proj/dashboards/my.dashboard.lookml",
            "dashboards/*.dashboard.lkml",
            "/proj",
        )


class TestYamlDashboardParsing:
    def test_yaml_dashboard_links_extracted(self):
        terms = parse_lookml_model(MODEL_PATH)
        with_links = [t for t in terms if t.dashboard_links]
        assert len(with_links) >= 1
        # total_revenue should have a link from the YAML dashboard
        revenue = [t for t in terms if t.field_id == "orders.total_revenue"]
        assert len(revenue) == 1
        dash_titles = [dl.title for dl in revenue[0].dashboard_links]
        assert "Revenue Overview Dashboard" in dash_titles

    def test_parse_yaml_dashboard_directly(self):
        from lookml_glossary.parser import _parse_yaml_dashboard
        dash_path = os.path.join(EXAMPLES_DIR, "revenue.dashboard.lookml")
        result = _parse_yaml_dashboard(dash_path)
        assert "dashboards" in result
        assert len(result["dashboards"]) == 1
        dash = result["dashboards"][0]
        assert dash["name"] == "revenue_overview"
        assert dash["title"] == "Revenue Overview Dashboard"
        assert len(dash["elements"]) == 2
        # First element should have 2 fields
        assert "orders.total_revenue" in dash["elements"][0]["fields"]
        assert "orders.order_count" in dash["elements"][0]["fields"]

    def test_yaml_dashboard_empty_file(self):
        from lookml_glossary.parser import _parse_yaml_dashboard
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".dashboard.lookml", mode="w", delete=False) as f:
            f.write("")
            tmp_path = f.name
        try:
            result = _parse_yaml_dashboard(tmp_path)
            assert result == {"dashboards": []}
        finally:
            os.unlink(tmp_path)

    def test_yaml_dashboard_invalid_yaml(self):
        from lookml_glossary.parser import _parse_yaml_dashboard
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".dashboard.lookml", mode="w", delete=False) as f:
            f.write(": : : invalid yaml {{{\n")
            tmp_path = f.name
        try:
            result = _parse_yaml_dashboard(tmp_path)
            assert result == {"dashboards": []}
        finally:
            os.unlink(tmp_path)


class TestSingleView:
    def test_extract_from_view_dict(self):
        parsed = parse_lookml_file(os.path.join(EXAMPLES_DIR, "orders.view.lkml"))
        view = parsed["views"][0]
        terms = extract_terms_from_view(view, model_name="test")
        assert any(t.name == "Total Revenue" for t in terms)
        assert any(t.is_kpi for t in terms)
        assert not any(t.term_type == "view" for t in terms)

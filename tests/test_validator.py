"""Tests for the LookML glossary drift validator."""

import io
import json
import os

import pytest

from lookml_glossary.generator import generate_json
from lookml_glossary.parser import GlossaryTerm, parse_lookml_model
from lookml_glossary.validator import (
    DriftItem,
    filter_by_severity,
    format_json,
    format_text,
    load_snapshot,
    validate,
)

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")
MODEL_PATH = os.path.join(EXAMPLES_DIR, "ecommerce.model.lkml")


def _generate_snapshot(terms):
    """Helper: generate a JSON snapshot and parse it back."""
    buf = io.StringIO()
    generate_json(terms, buf)
    buf.seek(0)
    data = json.loads(buf.getvalue())
    return data["glossary"]


class TestNoDrift:
    """Identical snapshot and fresh parse should produce zero drift items."""

    def test_no_drift(self):
        terms = parse_lookml_model(MODEL_PATH)
        snapshot = _generate_snapshot(terms)
        items = validate(snapshot, terms)
        assert items == []


class TestRemovedField:
    """A field present in the snapshot but missing from the fresh parse is an error."""

    def test_removed_field_detected(self):
        terms = parse_lookml_model(MODEL_PATH)
        snapshot = _generate_snapshot(terms)

        # Remove one field from current terms
        removed = terms.pop(0)
        items = validate(snapshot, terms)

        removed_items = [d for d in items if d.category == "removed_field"]
        assert len(removed_items) >= 1
        assert any(d.field_id == removed.field_id for d in removed_items)
        assert all(d.severity == "error" for d in removed_items)


class TestNewField:
    """A field present in fresh parse but not in the snapshot is a warning."""

    def test_new_field_detected(self):
        terms = parse_lookml_model(MODEL_PATH)
        snapshot = _generate_snapshot(terms)

        # Add a synthetic new field to current terms
        terms.append(GlossaryTerm(
            name="Brand New Metric",
            description="A test metric",
            term_type="measure",
            field_id="test_view.brand_new_metric",
        ))
        items = validate(snapshot, terms)

        new_items = [d for d in items if d.category == "new_field"]
        assert len(new_items) >= 1
        assert any(d.field_id == "test_view.brand_new_metric" for d in new_items)
        assert all(d.severity == "warning" for d in new_items)


class TestSqlChanged:
    """A change in sql_expression is a warning."""

    def test_sql_change_detected(self):
        terms = parse_lookml_model(MODEL_PATH)
        snapshot = _generate_snapshot(terms)

        # Mutate SQL on a field that has one
        target = next((t for t in terms if t.sql_expression), None)
        assert target is not None, "Need at least one term with sql_expression"
        target.sql_expression = "${totally_different_column}"

        items = validate(snapshot, terms)
        sql_items = [d for d in items if d.category == "sql_changed"]
        assert len(sql_items) >= 1
        assert any(d.field_id == target.field_id for d in sql_items)
        assert all(d.severity == "warning" for d in sql_items)


class TestTypeChanged:
    """A change in term_type or measure_type is an error."""

    def test_type_change_detected(self):
        terms = parse_lookml_model(MODEL_PATH)
        snapshot = _generate_snapshot(terms)

        # Find a dimension and flip it to measure
        target = next((t for t in terms if t.term_type == "dimension"), None)
        assert target is not None
        target.term_type = "measure"

        items = validate(snapshot, terms)
        type_items = [d for d in items if d.category == "type_changed"]
        assert len(type_items) >= 1
        assert any(d.field_id == target.field_id for d in type_items)
        assert all(d.severity == "error" for d in type_items)


class TestSeverityFilter:
    """Severity filtering hides items below threshold."""

    def test_error_only(self):
        items = [
            DriftItem("removed_field", "error", "v.a", "removed"),
            DriftItem("sql_changed", "warning", "v.b", "sql changed"),
            DriftItem("description_changed", "info", "v.c", "desc changed"),
        ]
        filtered = filter_by_severity(items, "error")
        assert len(filtered) == 1
        assert filtered[0].severity == "error"

    def test_warning_and_above(self):
        items = [
            DriftItem("removed_field", "error", "v.a", "removed"),
            DriftItem("sql_changed", "warning", "v.b", "sql changed"),
            DriftItem("description_changed", "info", "v.c", "desc changed"),
        ]
        filtered = filter_by_severity(items, "warning")
        assert len(filtered) == 2

    def test_all_severities(self):
        items = [
            DriftItem("removed_field", "error", "v.a", "removed"),
            DriftItem("sql_changed", "warning", "v.b", "sql changed"),
            DriftItem("description_changed", "info", "v.c", "desc changed"),
        ]
        filtered = filter_by_severity(items, "info")
        assert len(filtered) == 3


class TestExitCode:
    """Validate that --fail-on logic works via the item filtering."""

    def test_fail_on_warning_with_warnings(self):
        """If fail_on=warning and we have warnings, there should be failing items."""
        from lookml_glossary.validator import SEVERITY_RANK

        items = [
            DriftItem("sql_changed", "warning", "v.b", "sql changed"),
            DriftItem("description_changed", "info", "v.c", "desc changed"),
        ]
        fail_threshold = SEVERITY_RANK["warning"]
        failing = [d for d in items if SEVERITY_RANK.get(d.severity, 99) <= fail_threshold]
        assert len(failing) == 1

    def test_fail_on_error_with_only_warnings(self):
        """If fail_on=error but only warnings exist, no failing items."""
        from lookml_glossary.validator import SEVERITY_RANK

        items = [
            DriftItem("sql_changed", "warning", "v.b", "sql changed"),
        ]
        fail_threshold = SEVERITY_RANK["error"]
        failing = [d for d in items if SEVERITY_RANK.get(d.severity, 99) <= fail_threshold]
        assert len(failing) == 0


class TestJsonOutput:
    """format_json produces valid JSON with the correct structure."""

    def test_json_structure(self):
        items = [
            DriftItem("removed_field", "error", "v.a", "removed", old_value="old"),
            DriftItem("sql_changed", "warning", "v.b", "sql changed", old_value="x", new_value="y"),
        ]
        output = format_json(items)
        data = json.loads(output)

        assert "drift" in data
        assert "summary" in data
        assert len(data["drift"]) == 2
        assert data["summary"]["total"] == 2
        assert data["summary"]["errors"] == 1
        assert data["summary"]["warnings"] == 1

    def test_json_fields(self):
        items = [DriftItem("new_field", "warning", "v.x", "new field found")]
        data = json.loads(format_json(items))
        entry = data["drift"][0]
        assert entry["category"] == "new_field"
        assert entry["severity"] == "warning"
        assert entry["field_id"] == "v.x"


class TestTextOutput:
    """format_text produces readable text."""

    def test_no_drift_message(self):
        assert format_text([]) == "No drift detected."

    def test_summary_line(self):
        items = [
            DriftItem("removed_field", "error", "v.a", "removed"),
            DriftItem("sql_changed", "warning", "v.b", "changed"),
        ]
        output = format_text(items)
        assert "1 error(s)" in output
        assert "1 warning(s)" in output

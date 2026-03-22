"""Tests for Liquid template branch extraction."""

import os

import pytest

from lookml_glossary.liquid import (
    has_liquid,
    extract_liquid_branches,
)
from lookml_glossary.parser import (
    GlossaryTerm,
    parse_lookml_file,
    extract_terms_from_view,
)
from lookml_glossary.enrichment import _extract_sql_tables


EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


# ---------------------------------------------------------------------------
# has_liquid detection
# ---------------------------------------------------------------------------

class TestHasLiquid:
    def test_plain_sql(self):
        assert not has_liquid("${TABLE}.id")

    def test_if_tag(self):
        assert has_liquid("{% if x %}col{% endif %}")

    def test_case_tag(self):
        assert has_liquid("{% case x %}{% when 1 %}a{% endcase %}")

    def test_looker_refs_not_liquid(self):
        # ${TABLE}.col and ${view.SQL_TABLE_NAME} are NOT Liquid
        assert not has_liquid("${TABLE}.id")
        assert not has_liquid("${orders.SQL_TABLE_NAME}")


# ---------------------------------------------------------------------------
# Branch extraction — if/elsif/else
# ---------------------------------------------------------------------------

class TestIfBranches:
    def test_simple_if_else(self):
        sql = "{% if x %}col_a{% else %}col_b{% endif %}"
        branches = extract_liquid_branches(sql)
        assert "col_a" in branches
        assert "col_b" in branches

    def test_if_elsif_else(self):
        sql = "{% if x == 1 %}col_a{% elsif x == 2 %}col_b{% else %}col_c{% endif %}"
        branches = extract_liquid_branches(sql)
        assert len(branches) == 3
        assert "col_a" in branches
        assert "col_b" in branches
        assert "col_c" in branches

    def test_surrounding_text_preserved(self):
        sql = "SELECT {% if x %}col_a{% else %}col_b{% endif %} FROM t"
        branches = extract_liquid_branches(sql)
        assert any("SELECT" in b and "col_a" in b and "FROM t" in b for b in branches)
        assert any("SELECT" in b and "col_b" in b and "FROM t" in b for b in branches)

    def test_nested_if(self):
        sql = (
            "{% if a %}"
            "{% if b %}inner_1{% else %}inner_2{% endif %}"
            "{% else %}outer{% endif %}"
        )
        branches = extract_liquid_branches(sql)
        assert any("inner_1" in b for b in branches)
        assert any("inner_2" in b for b in branches)
        assert any("outer" in b for b in branches)


# ---------------------------------------------------------------------------
# Branch extraction — case/when
# ---------------------------------------------------------------------------

class TestCaseBranches:
    def test_case_when(self):
        sql = "{% case x %}{% when 1 %}val_1{% when 2 %}val_2{% else %}val_default{% endcase %}"
        branches = extract_liquid_branches(sql)
        assert "val_1" in branches
        assert "val_2" in branches
        assert "val_default" in branches


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_liquid_returns_original(self):
        sql = "${TABLE}.id"
        branches = extract_liquid_branches(sql)
        assert branches == ["${TABLE}.id"]

    def test_empty_string(self):
        branches = extract_liquid_branches("")
        assert branches == [""]

    def test_branch_limit(self):
        """Deeply nested templates should not explode combinatorially."""
        # 8 levels of if/else = 256 combinations, but limit is 64
        sql = ""
        for i in range(8):
            sql += f"{{% if v{i} %}}a{i}"
        for i in range(8):
            sql += f"{{% else %}}b{i}{{% endif %}}"
        branches = extract_liquid_branches(sql)
        assert len(branches) <= 64

    def test_looker_user_attributes(self):
        sql = """{% if _user_attributes['region'] == 'EU' %}
            ${TABLE}.region_eu
        {% else %}
            ${TABLE}.region_global
        {% endif %}"""
        branches = extract_liquid_branches(sql)
        assert len(branches) == 2
        assert any("region_eu" in b for b in branches)
        assert any("region_global" in b for b in branches)


# ---------------------------------------------------------------------------
# Integration with parser — dynamic_fields.view.lkml
# ---------------------------------------------------------------------------

class TestParserIntegration:
    @pytest.fixture
    def dynamic_terms(self):
        parsed = parse_lookml_file(os.path.join(EXAMPLES_DIR, "dynamic_fields.view.lkml"))
        view = parsed["views"][0]
        return extract_terms_from_view(view, model_name="test")

    def test_dynamic_sql_flagged(self, dynamic_terms):
        region = [t for t in dynamic_terms if t.name == "Region Column"]
        assert len(region) == 1
        assert region[0].is_dynamic_sql is True

    def test_branches_populated(self, dynamic_terms):
        region = [t for t in dynamic_terms if t.name == "Region Column"]
        assert len(region[0].sql_branches) >= 2
        # All three branches should be present
        branch_text = " ".join(region[0].sql_branches)
        assert "region_eu" in branch_text
        assert "region_apac" in branch_text
        assert "region_global" in branch_text

    def test_non_liquid_not_flagged(self, dynamic_terms):
        # The view itself doesn't have non-liquid fields, but check the flag
        for t in dynamic_terms:
            if not has_liquid(t.sql_expression or ""):
                assert t.is_dynamic_sql is False
                assert t.sql_branches == []

    def test_case_when_branches(self, dynamic_terms):
        case_term = [t for t in dynamic_terms if t.name == "Case Example"]
        assert len(case_term) == 1
        assert case_term[0].is_dynamic_sql is True
        branch_text = " ".join(case_term[0].sql_branches)
        assert "gold_price" in branch_text
        assert "silver_price" in branch_text
        assert "standard_price" in branch_text


# ---------------------------------------------------------------------------
# Integration with enrichment — derived table SQL with Liquid
# ---------------------------------------------------------------------------

class TestEnrichmentIntegration:
    def test_liquid_in_derived_table_sql(self):
        sql = """
        SELECT *
        FROM {% if _user_attributes['env'] == 'prod' %}
            prod_schema.events
        {% else %}
            staging_schema.events
        {% endif %}
        """
        tables, refs = _extract_sql_tables(sql)
        table_names = {t["full_name"] for t in tables}
        assert "prod_schema.events" in table_names
        assert "staging_schema.events" in table_names

    def test_liquid_with_view_refs(self):
        sql = """
        SELECT *
        FROM {% if x %}
            ${orders.SQL_TABLE_NAME}
        {% else %}
            ${archived_orders.SQL_TABLE_NAME}
        {% endif %}
        """
        tables, refs = _extract_sql_tables(sql)
        assert "orders" in refs
        assert "archived_orders" in refs

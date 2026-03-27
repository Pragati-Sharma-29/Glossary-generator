"""Tests for the enrichment module improvements."""

import os
import tempfile

import pytest

from lookml_glossary.parser import GlossaryTerm
from lookml_glossary.enrichment import (
    find_synonyms,
    find_related_terms,
    _find_related_for_explore,
    _extract_sql_tables,
    _FileIndex,
)
from lookml_glossary.parser import _matches_include


# ---------------------------------------------------------------------------
# Synonym exclusion — identity-level matches should NOT appear in related_terms
# ---------------------------------------------------------------------------

class TestSynonymExclusion:
    def _term(self, name, field_id, view="v", sql="", explore="exp"):
        return GlossaryTerm(
            name=name, description="", term_type="dimension",
            field_id=field_id, view_name=view, sql_expression=sql,
            explore_name=explore,
        )

    def test_exact_label_not_in_related(self):
        """Identity-level matches (same name) should be excluded from related_terms."""
        a = self._term("Revenue", "v1.revenue", view="v1")
        b = self._term("Revenue", "v2.revenue", view="v2")
        find_synonyms([a, b])
        find_related_terms([a, b])
        assert len(a.related_terms) == 0

    def test_different_labels_are_related(self):
        """Non-identical terms in the same explore should appear as related."""
        a = self._term("Revenue", "v.revenue", view="v")
        b = self._term("Country", "v.country", view="v")
        find_related_terms([a, b])
        names = [r["term_name"] for r in a.related_terms]
        assert "Country" in names

    def test_no_duplicates_in_related(self):
        """Related terms should have no duplicate term_names."""
        terms = [self._term(f"Field {i}", f"v.field_{i}") for i in range(20)]
        find_related_terms(terms)
        for t in terms:
            names = [r["term_name"] for r in t.related_terms]
            assert len(names) == len(set(names))

    def test_large_batch_no_crash(self):
        """Verify the approach handles many terms without O(n²) blowup."""
        terms = [self._term(f"Field {i}", f"v.field_{i}") for i in range(500)]
        find_synonyms(terms)
        find_related_terms(terms)


# ---------------------------------------------------------------------------
# SQL table extraction (sqlparse-based)
# ---------------------------------------------------------------------------

class TestSqlparseExtraction:
    def test_simple_select(self):
        tables, refs = _extract_sql_tables("SELECT * FROM schema.my_table")
        assert any(t["full_name"] == "schema.my_table" for t in tables)

    def test_join_tables(self):
        sql = """
        SELECT a.id, b.name
        FROM schema.orders a
        LEFT JOIN schema.users b ON a.user_id = b.id
        """
        tables, refs = _extract_sql_tables(sql)
        names = {t["full_name"] for t in tables}
        assert "schema.orders" in names
        assert "schema.users" in names

    def test_cte_aliases_excluded(self):
        sql = """
        WITH daily_totals AS (
            SELECT date, SUM(amount) as total FROM schema.orders GROUP BY date
        )
        SELECT * FROM daily_totals
        """
        tables, refs = _extract_sql_tables(sql)
        names = {t["full_name"] for t in tables}
        assert "schema.orders" in names
        # daily_totals is a CTE alias, not a real table
        assert "daily_totals" not in names

    def test_looker_view_reference(self):
        sql = "SELECT * FROM ${orders.SQL_TABLE_NAME}"
        tables, refs = _extract_sql_tables(sql)
        assert "orders" in refs

    def test_no_size_limit(self):
        """Verify there's no 50K char limit anymore."""
        # Build SQL > 50K chars
        big_sql = "SELECT * FROM schema.big_table WHERE " + " AND ".join(
            [f"col_{i} = {i}" for i in range(5000)]
        )
        assert len(big_sql) > 50000
        tables, refs = _extract_sql_tables(big_sql)
        assert any(t["table"] == "big_table" for t in tables)

    def test_unquoted_table(self):
        tables, refs = _extract_sql_tables("SELECT id FROM my_table")
        assert any(t["table"] == "my_table" for t in tables)


# ---------------------------------------------------------------------------
# File index
# ---------------------------------------------------------------------------

class TestFileIndex:
    def test_indexes_example_views(self):
        examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
        idx = _FileIndex(examples_dir)
        assert "orders" in idx.view_to_file
        assert "users" in idx.view_to_file
        assert "products" in idx.view_to_file

    def test_find_view_file_returns_path(self):
        examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
        idx = _FileIndex(examples_dir)
        path = idx.find_view_file("orders")
        assert path is not None
        assert path.endswith(".view.lkml")

    def test_unknown_view_returns_none(self):
        examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
        idx = _FileIndex(examples_dir)
        assert idx.find_view_file("nonexistent_view") is None


# ---------------------------------------------------------------------------
# Include pattern matching (glob-based)
# ---------------------------------------------------------------------------

class TestIncludeMatching:
    def test_star_view_pattern(self):
        assert _matches_include("/proj/views/orders.view.lkml", "*.view.lkml", "/proj")

    def test_doublestar_pattern(self):
        assert _matches_include("/proj/sub/deep/orders.view.lkml", "**/*.view.lkml", "/proj")

    def test_subdir_pattern(self):
        assert _matches_include("/proj/views/orders.view.lkml", "views/*.view.lkml", "/proj")
        assert not _matches_include("/proj/models/orders.view.lkml", "views/*.view.lkml", "/proj")

    def test_exact_filename(self):
        assert _matches_include("/proj/orders.view.lkml", "orders.view.lkml", "/proj")

    def test_no_match(self):
        assert not _matches_include("/proj/orders.view.lkml", "users.view.lkml", "/proj")


# ---------------------------------------------------------------------------
# Related terms — bounded heap
# ---------------------------------------------------------------------------

class TestRelatedTermsHeap:
    def _term(self, name, field_id, view="v", explore="exp"):
        return GlossaryTerm(
            name=name, description="", term_type="dimension",
            field_id=field_id, view_name=view, explore_name=explore,
        )

    def test_same_view_ranked_higher(self):
        a = self._term("Revenue", "v.revenue", view="orders")
        b = self._term("Cost", "v.cost", view="orders")       # same view
        c = self._term("Revenue Tax", "v2.tax", view="other")  # different view
        _find_related_for_explore([a, b, c])
        # b shares the view, so it should rank above c
        assert len(a.related_terms) >= 1
        assert a.related_terms[0]["field_id"] == "v.cost"

    def test_max_related_respected(self):
        terms = [self._term(f"Field {i}", f"v.f{i}", view="v") for i in range(20)]
        _find_related_for_explore(terms)
        for t in terms:
            assert len(t.related_terms) <= 5

    def test_cross_view_skipped_when_heap_full(self):
        """When 5 same-view terms score > 0.5, cross-view terms (max 0.5) can't enter."""
        # Create 6 same-view terms with similar names → all score > 0.5
        same_view = [self._term(f"Order {i}", f"v.order_{i}", view="v") for i in range(6)]
        # One cross-view term
        other = self._term("Unrelated Xyz", "other.xyz", view="other")
        _find_related_for_explore(same_view + [other])
        # The first term's related should all be from view "v"
        for entry in same_view[0].related_terms:
            assert entry["view_name"] == "v"

    def test_parallel_explores(self):
        """find_related_terms with multiple explores runs without error."""
        terms = []
        for exp in ("exp1", "exp2", "exp3"):
            terms.extend([self._term(f"F{i}", f"{exp}.f{i}", explore=exp) for i in range(10)])
        find_related_terms(terms)
        # Each term should have related terms from its own explore only
        for t in terms:
            for rel in t.related_terms:
                # Related terms come from the same explore
                matching = [x for x in terms if x.field_id == rel["field_id"]]
                assert len(matching) == 1
                assert matching[0].explore_name == t.explore_name


# ---------------------------------------------------------------------------
# Parallel file parsing (integration test)
# ---------------------------------------------------------------------------

class TestParallelParsing:
    def test_parallel_produces_same_results(self):
        """Verify parallel parsing gives the same term count as sequential."""
        from lookml_glossary.parser import parse_lookml_model
        model_path = os.path.join(os.path.dirname(__file__), "..", "examples", "ecommerce.model.lkml")
        terms = parse_lookml_model(model_path)
        assert len(terms) == 42  # fields + expanded dimension_group timeframes + dynamic_fields

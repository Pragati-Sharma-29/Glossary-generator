"""Enrich glossary terms with synonyms, related terms, and related entries."""

import heapq
import logging
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import sqlparse
from sqlparse.sql import Identifier, IdentifierList, Where
from sqlparse.tokens import Keyword, DML

from .parser import GlossaryTerm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# String similarity helpers
# ---------------------------------------------------------------------------


def _normalize(name: str) -> str:
    """Normalize a term name for comparison: lowercase, strip common prefixes."""
    s = name.lower().strip()
    # Remove common aggregation prefixes for comparison
    for prefix in ("total ", "sum of ", "count of ", "average ", "avg ", "num ", "number of "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s


def _tokenize(name: str) -> frozenset[str]:
    """Split a name into a frozenset of meaningful word tokens."""
    return frozenset(re.split(r"[\s_]+", name.lower().strip())) - {"", "of", "the", "a", "an", "in", "for"}


def _label_similarity(a: str, b: str) -> float:
    """Compute similarity between two labels (0.0 to 1.0).

    Uses token overlap (Jaccard-like) with a bonus for substring containment.
    """
    na, nb = _normalize(a), _normalize(b)
    if na == nb:
        return 1.0
    # Substring containment
    if na in nb or nb in na:
        return 0.9
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# 1. Synonym detection — O(n) via hash-bucket indexing
# ---------------------------------------------------------------------------

_SYNONYM_THRESHOLD = 0.7


def _add_synonym_pair(a: GlossaryTerm, b: GlossaryTerm) -> None:
    """Link two terms as synonyms of each other (no duplicates)."""
    syn_b = {"term_name": b.name, "field_id": b.field_id,
             "view_name": b.view_name or "", "explore_name": b.explore_name or ""}
    syn_a = {"term_name": a.name, "field_id": a.field_id,
             "view_name": a.view_name or "", "explore_name": a.explore_name or ""}
    if syn_b not in a.synonyms:
        a.synonyms.append(syn_b)
    if syn_a not in b.synonyms:
        b.synonyms.append(syn_a)


def find_synonyms(terms: list[GlossaryTerm]) -> None:
    """Find synonym terms using hash-bucket indexing instead of O(n²) comparison.

    Two bucket strategies find most matches in O(n) average time:
      1. Exact normalized label — fields with the same normalized name
      2. Same view+SQL — fields sharing the underlying column

    A token-overlap pass catches remaining near-matches for smaller buckets.
    Mutates terms in-place.
    """
    # --- Bucket 1: exact normalized label ---
    label_buckets: dict[str, list[GlossaryTerm]] = defaultdict(list)
    for t in terms:
        label_buckets[_normalize(t.name)].append(t)

    for bucket in label_buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                if a.field_id != b.field_id:
                    _add_synonym_pair(a, b)

    # --- Bucket 2: same view + SQL expression ---
    sql_buckets: dict[tuple[str, str], list[GlossaryTerm]] = defaultdict(list)
    for t in terms:
        if t.view_name and t.sql_expression:
            sql_buckets[(t.view_name, t.sql_expression)].append(t)

    for bucket in sql_buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                if a.field_id != b.field_id:
                    _add_synonym_pair(a, b)

    # --- Bucket 3: token-overlap for near-matches ---
    # Index terms by each of their tokens so we only compare terms sharing
    # at least one meaningful word (required for Jaccard >= 0.7).
    token_index: dict[str, list[GlossaryTerm]] = defaultdict(list)
    for t in terms:
        for tok in _tokenize(t.name):
            token_index[tok].append(t)

    seen_pairs: set[tuple[str, str]] = set()
    for candidates in token_index.values():
        if len(candidates) < 2 or len(candidates) > 500:
            # Skip tokens that are too common (e.g., "id") to keep O(n)-ish
            continue
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = candidates[i], candidates[j]
                if a.field_id == b.field_id:
                    continue
                pair_key = (min(a.field_id, b.field_id), max(a.field_id, b.field_id))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                if _label_similarity(a.name, b.name) >= _SYNONYM_THRESHOLD:
                    _add_synonym_pair(a, b)


# ---------------------------------------------------------------------------
# 2. Related terms detection
# ---------------------------------------------------------------------------

_MAX_RELATED = 5


def _find_related_for_explore(explore_terms: list[GlossaryTerm]) -> None:
    """Find related terms within a single explore using a bounded min-heap.

    For each term, maintains a heap of size _MAX_RELATED so we never store
    more than K candidates — turning the O(n·n·log n) sort into O(n·log K).
    Same-view terms get a +0.5 bonus, so we pre-bucket by view to guarantee
    those are scored first and can raise the heap threshold early, allowing
    many cross-view comparisons to be skipped entirely.
    """
    # Pre-bucket by view for fast same-view lookups
    by_view: dict[str, list[GlossaryTerm]] = defaultdict(list)
    for t in explore_terms:
        by_view[t.view_name or ""].append(t)

    for a in explore_terms:
        # Min-heap of (score, tiebreaker, term) — bounded at _MAX_RELATED
        heap: list[tuple[float, int, GlossaryTerm]] = []
        min_score = 0.1  # threshold to beat to enter the heap
        tie = 0  # tiebreaker for heap ordering (avoids comparing GlossaryTerm)

        # Phase 1: same-view terms (guaranteed +0.5 base score)
        if a.view_name:
            for b in by_view[a.view_name]:
                if a.field_id == b.field_id:
                    continue
                score = 0.5 + _label_similarity(a.name, b.name) * 0.5
                if score > min_score:
                    if len(heap) < _MAX_RELATED:
                        heapq.heappush(heap, (score, tie, b))
                    else:
                        heapq.heapreplace(heap, (score, tie, b))
                        min_score = heap[0][0]
                    tie += 1

        # Phase 2: cross-view terms — skip if they can't beat the heap minimum
        # Max possible cross-view score is 0.0 (no view bonus) + 0.5 (perfect label) = 0.5
        if min_score < 0.5:
            for b in explore_terms:
                if a.field_id == b.field_id:
                    continue
                # Skip same-view (already scored above)
                if a.view_name and a.view_name == b.view_name:
                    continue
                score = _label_similarity(a.name, b.name) * 0.5
                if score > min_score:
                    if len(heap) < _MAX_RELATED:
                        heapq.heappush(heap, (score, tie, b))
                    else:
                        heapq.heapreplace(heap, (score, tie, b))
                        min_score = heap[0][0]
                    tie += 1

        # Extract top-K in descending order
        top_k = sorted(heap, key=lambda x: x[0], reverse=True)
        for score, _, b in top_k:
            entry = {"term_name": b.name, "field_id": b.field_id,
                     "type": b.term_type, "view_name": b.view_name or ""}
            if entry not in a.related_terms:
                a.related_terms.append(entry)


def find_related_terms(terms: list[GlossaryTerm]) -> None:
    """Find related terms: measures/dimensions in the SAME explore that share
    the same view name or have complementary labels. Mutates terms in-place.

    Each explore is processed independently, so they run in parallel threads.
    Within each explore, a bounded min-heap keeps only the top-K candidates
    per term, avoiding a full sort of all candidates.
    """
    # Group terms by explore
    by_explore: dict[str, list[GlossaryTerm]] = {}
    for t in terms:
        key = t.explore_name or "__no_explore__"
        by_explore.setdefault(key, []).append(t)

    # Process explores in parallel (each is independent)
    explores = list(by_explore.values())
    if len(explores) <= 1:
        for explore_terms in explores:
            _find_related_for_explore(explore_terms)
    else:
        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(_find_related_for_explore, et) for et in explores]
            for f in as_completed(futures):
                f.result()  # propagate exceptions


# ---------------------------------------------------------------------------
# 3. Related entries — source table resolution
# ---------------------------------------------------------------------------


def _load_manifest(project_root: str) -> tuple[dict[str, str], list[dict]]:
    """Load constants and project_import declarations from manifest.lkml.

    Returns (constants_dict, list_of_project_imports).
    """
    manifest_path = os.path.join(project_root, "manifest.lkml")
    constants: dict[str, str] = {}
    imports: list[dict] = []
    if not os.path.exists(manifest_path):
        logger.info("No manifest.lkml found. Templated sql_table_name values may not resolve correctly.")
        return constants, imports
    try:
        with open(manifest_path, "r") as f:
            content = f.read()
        # Constants
        for m in re.finditer(r'constant:\s*(\w+)\s*\{\s*value:\s*"([^"]*)"', content):
            constants[m.group(1)] = m.group(2)
        logger.info("Loaded %d manifest constants.", len(constants))

        # Project imports: remote_dependency or local_dependency
        for m in re.finditer(
            r'(?:remote_dependency|local_dependency):\s*(\w+)\s*\{([^}]*)\}',
            content, re.DOTALL,
        ):
            imp: dict[str, str] = {"name": m.group(1)}
            url_m = re.search(r'url:\s*"([^"]*)"', m.group(2))
            if url_m:
                imp["url"] = url_m.group(1)
            override_m = re.search(r'override_constant:\s*(\w+)\s*\{\s*value:\s*"([^"]*)"', m.group(2))
            if override_m:
                constants[override_m.group(1)] = override_m.group(2)
            imports.append(imp)

        if imports:
            logger.info("Found %d project import(s): %s",
                         len(imports), ", ".join(i["name"] for i in imports))
    except Exception:
        logger.warning("Failed to read manifest.lkml.")
    return constants, imports


def _is_safe_path(filepath: str, project_root: str) -> bool:
    """Verify a resolved path stays within the project root."""
    real = os.path.realpath(filepath)
    root = os.path.realpath(project_root)
    return real.startswith(root + os.sep) or real == root


# ---------------------------------------------------------------------------
# File index — scan once, look up O(1) per view
# ---------------------------------------------------------------------------


class _FileIndex:
    """One-time index of all LookML files in the project for O(1) view lookups."""

    def __init__(self, project_root: str, extra_roots: list[str] | None = None):
        self.project_root = os.path.realpath(project_root)
        self._search_roots = [self.project_root]
        for r in (extra_roots or []):
            real = os.path.realpath(r)
            if real not in self._search_roots:
                self._search_roots.append(real)

        # view_name -> filepath (first match wins)
        self.view_to_file: dict[str, str] = {}
        # filepath -> file content (lazy-populated on demand)
        self.file_cache: dict[str, str] = {}
        # all lkml files found
        self.all_lkml_files: list[str] = []

        self._build_index()

    def _build_index(self) -> None:
        """Walk all search roots once and index every view definition."""
        view_pattern = re.compile(r'view:\s*(\w+)\s*\{')
        for search_root in self._search_roots:
            for root, _, filenames in os.walk(search_root, followlinks=False):
                for fn in filenames:
                    if not fn.endswith(".lkml"):
                        continue
                    fpath = os.path.realpath(os.path.join(root, fn))
                    if not any(fpath.startswith(sr + os.sep) or fpath == sr
                               for sr in self._search_roots):
                        continue
                    self.all_lkml_files.append(fpath)
                    if ".view." in fn or fn.endswith(".view.lkml"):
                        content = self._read(fpath)
                        if content:
                            for m in view_pattern.finditer(content):
                                vname = m.group(1)
                                if vname not in self.view_to_file:
                                    self.view_to_file[vname] = fpath
                    elif fn.endswith((".model.lkml", ".explore.lkml")):
                        content = self._read(fpath)
                        if content:
                            for m in view_pattern.finditer(content):
                                vname = m.group(1)
                                if vname not in self.view_to_file:
                                    self.view_to_file[vname] = fpath

        logger.info("File index built: %d LookML files, %d view definitions.",
                     len(self.all_lkml_files), len(self.view_to_file))

    def _read(self, filepath: str) -> Optional[str]:
        if filepath in self.file_cache:
            return self.file_cache[filepath]
        try:
            with open(filepath, "r") as f:
                content = f.read()
            self.file_cache[filepath] = content
            return content
        except Exception:
            return None

    def find_view_file(self, view_name: str) -> Optional[str]:
        """O(1) lookup for the file containing a view definition."""
        return self.view_to_file.get(view_name)

    def read_cached(self, filepath: str) -> Optional[str]:
        return self._read(filepath)


def _extract_view_block(content: str, view_name: str) -> Optional[str]:
    """Extract the view block for a specific view from file content."""
    pattern = re.compile(r'view:\s*' + re.escape(view_name) + r'\s*\{')
    match = pattern.search(content)
    if not match:
        return None
    start = match.start()
    # Find the matching closing brace
    depth = 0
    i = match.end() - 1  # position of the opening {
    while i < len(content):
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth == 0:
                return content[start:i + 1]
        i += 1
    return None


def _resolve_templated_value(raw: str, constants: dict[str, str]) -> str:
    """Resolve ${constant} and @{constant} references in sql_table_name."""
    def replacer(m):
        name = m.group(1)
        if name in constants:
            return constants[name]
        logger.warning("Unresolved constant '%s' in sql_table_name for view resolution.", name)
        return f"unknown_{name}"

    result = re.sub(r'\$\{(\w+)\}', replacer, raw)
    result = re.sub(r'@\{(\w+)\}', replacer, result)
    return result


def _parse_table_name(raw: str) -> tuple[str, str]:
    """Parse a raw table name into (schema, table). Strips quotes/backticks."""
    cleaned = raw.strip().strip('`"\'')
    # Handle quoted parts like "schema"."table"
    cleaned = re.sub(r'["`]', '', cleaned)
    if '.' in cleaned:
        parts = cleaned.split('.', 1)
        return parts[0].strip(), parts[1].strip()
    return "", cleaned


# ---------------------------------------------------------------------------
# SQL table extraction — sqlparse-based (no size limit)
# ---------------------------------------------------------------------------

# Pre-resolve Looker ${view.SQL_TABLE_NAME} references before feeding to sqlparse
_LOOKER_VIEW_REF = re.compile(r'\$\{(\w+)\.SQL_TABLE_NAME\}', re.IGNORECASE)


def _extract_sql_tables(sql: str) -> tuple[list[dict], list[str]]:
    """Extract physical table refs and Looker view references from derived_table SQL.

    Uses sqlparse for robust extraction with no size limit.
    If the SQL contains Liquid templates, extracts tables from all branches.
    """
    from .liquid import has_liquid, extract_liquid_branches

    # If SQL contains Liquid templates, extract tables from all branches
    if has_liquid(sql):
        all_tables: list[dict] = []
        all_views: list[str] = []
        seen_tables: set[str] = set()
        seen_views: set[str] = set()
        for branch in extract_liquid_branches(sql):
            tables, views = _extract_sql_tables_single(branch)
            for t in tables:
                if t["full_name"] not in seen_tables:
                    seen_tables.add(t["full_name"])
                    all_tables.append(t)
            for v in views:
                if v not in seen_views:
                    seen_views.add(v)
                    all_views.append(v)
        return all_tables, all_views

    return _extract_sql_tables_single(sql)


def _extract_sql_tables_single(sql: str) -> tuple[list[dict], list[str]]:
    """Extract tables from a single (non-Liquid) SQL string."""
    source_tables: list[dict] = []
    view_references: list[str] = []

    # Extract Looker view references before parsing (sqlparse can't handle ${} syntax)
    for m in _LOOKER_VIEW_REF.finditer(sql):
        view_references.append(m.group(1))

    # Replace Looker refs with placeholders so sqlparse doesn't choke
    cleaned_sql = _LOOKER_VIEW_REF.sub("__looker_placeholder__", sql)
    # Also strip Looker ${TABLE}.col and ${view.col} references
    cleaned_sql = re.sub(r'\$\{[^}]+\}', '__looker_ref__', cleaned_sql)

    try:
        parsed = sqlparse.parse(cleaned_sql)
    except Exception:
        logger.warning("sqlparse failed to parse SQL (%d chars); falling back to regex.", len(sql))
        return _extract_sql_tables_regex(sql)

    # Collect CTE aliases to exclude
    cte_aliases: set[str] = set()

    for statement in parsed:
        _collect_cte_aliases(statement.tokens, cte_aliases)
        _collect_table_refs(statement.tokens, source_tables, cte_aliases)

    return source_tables, view_references


def _collect_cte_aliases(tokens, cte_aliases: set[str]) -> None:
    """Walk tokens to find WITH ... alias AS (...) patterns."""
    in_with = False
    for token in tokens:
        # sqlparse uses Keyword.CTE for WITH in CTE context
        if token.ttype in (Keyword, sqlparse.tokens.Keyword.CTE) and token.normalized == "WITH":
            in_with = True
            continue
        if in_with and token.ttype is DML:
            in_with = False
            continue
        if in_with and isinstance(token, Identifier):
            # The CTE alias is the real_name of the identifier (before AS)
            name = token.get_real_name()
            if name:
                cte_aliases.add(name.lower())
        if in_with and isinstance(token, IdentifierList):
            for ident in token.get_identifiers():
                if isinstance(ident, Identifier):
                    name = ident.get_real_name()
                    if name:
                        cte_aliases.add(name.lower())


def _collect_table_refs(tokens, source_tables: list[dict], cte_aliases: set[str]) -> None:
    """Walk tokens to find FROM/JOIN table references."""
    expect_table = False
    for token in tokens:
        # Skip WHERE clauses (subqueries handled recursively if needed)
        if isinstance(token, Where):
            continue

        if token.ttype is Keyword and token.normalized in (
            "FROM", "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN",
            "FULL JOIN", "CROSS JOIN", "LEFT OUTER JOIN", "RIGHT OUTER JOIN",
            "FULL OUTER JOIN",
        ):
            expect_table = True
            continue

        if expect_table:
            if isinstance(token, IdentifierList):
                for ident in token.get_identifiers():
                    if isinstance(ident, Identifier):
                        _add_table_from_identifier(ident, source_tables, cte_aliases)
                expect_table = False
            elif isinstance(token, Identifier):
                _add_table_from_identifier(token, source_tables, cte_aliases)
                expect_table = False
            elif not token.is_whitespace:
                expect_table = False

        # Recurse into parenthesized subqueries
        if hasattr(token, "tokens"):
            _collect_table_refs(token.tokens, source_tables, cte_aliases)


def _add_table_from_identifier(ident: Identifier, source_tables: list[dict],
                                cte_aliases: set[str]) -> None:
    """Extract a table name from a sqlparse Identifier and add to results."""
    real_name = ident.get_real_name()
    if not real_name or real_name.lower() in cte_aliases:
        return
    if real_name in ("__looker_placeholder__", "__looker_ref__"):
        return

    schema_part = ident.get_parent_name()
    full = f"{schema_part}.{real_name}" if schema_part else real_name
    # Skip if it looks like a subquery artifact
    if full.startswith("("):
        return
    source_tables.append({
        "full_name": full,
        "schema": schema_part or "",
        "table": real_name,
        "raw_value": full,
    })


def _extract_sql_tables_regex(sql: str) -> tuple[list[dict], list[str]]:
    """Regex fallback for SQL table extraction (used when sqlparse fails)."""
    source_tables: list[dict] = []
    view_references: list[str] = []

    for m in _LOOKER_VIEW_REF.finditer(sql):
        view_references.append(m.group(1))

    # CTE aliases
    cte_aliases: set[str] = set()
    cte_match = re.search(r'\bWITH\b(.*?)(?=\bSELECT\b)', sql, re.IGNORECASE | re.DOTALL)
    if cte_match:
        for alias_m in re.finditer(r'(\w+)\s+AS\s*\(', cte_match.group(1), re.IGNORECASE):
            cte_aliases.add(alias_m.group(1).lower())

    for m in re.finditer(r'(?:FROM|JOIN)\s+([^\s(,;]+)', sql, re.IGNORECASE):
        ref = m.group(1).strip().strip('`"')
        if ref.lower() in cte_aliases:
            continue
        if _LOOKER_VIEW_REF.match(ref):
            continue
        if ref.startswith('('):
            continue
        if '.' in ref:
            parts = ref.split('.', 1)
            source_tables.append({"full_name": ref, "schema": parts[0], "table": parts[1], "raw_value": ref})
        else:
            source_tables.append({"full_name": ref, "schema": "", "table": ref, "raw_value": ref})

    return source_tables, view_references


# ---------------------------------------------------------------------------
# View table resolution
# ---------------------------------------------------------------------------

_MAX_RECURSION_DEPTH = 10


def _resolve_view_table(
    view_name: str,
    project_root: str,
    constants: dict[str, str],
    file_index: _FileIndex,
    view_block_cache: dict[str, dict],
    depth: int = 0,
) -> Optional[dict]:
    """Resolve the source table for a view, with recursive resolution and caching."""
    if view_name in view_block_cache:
        return view_block_cache[view_name]

    if depth > _MAX_RECURSION_DEPTH:
        logger.warning("Recursion depth exceeded resolving view '%s'. Stopped at depth %d.",
                        view_name, _MAX_RECURSION_DEPTH)
        result = {
            "type": "table",
            "name": "unresolved_deep_reference",
            "id": view_name,
            "url": None,
            "source_type": "unresolved",
            "verified": False,
        }
        view_block_cache[view_name] = result
        return result

    filepath = file_index.find_view_file(view_name)
    if not filepath:
        logger.warning("No view file found for view '%s' — table related_entry skipped.", view_name)
        view_block_cache[view_name] = None
        return None

    content = file_index.read_cached(filepath)
    if not content:
        view_block_cache[view_name] = None
        return None

    block = _extract_view_block(content, view_name)
    if not block:
        view_block_cache[view_name] = None
        return None

    # Pattern 1: sql_table_name
    sql_table_m = re.search(r'sql_table_name:\s*(.+?)\s*;;', block)
    if sql_table_m:
        raw = sql_table_m.group(1).strip()
        resolved = _resolve_templated_value(raw, constants)
        schema, table = _parse_table_name(resolved)
        if not schema:
            schema = constants.get("schema", "unknown_schema")
        full_name = f"{schema}.{table}" if schema else table
        result = {
            "type": "table",
            "name": full_name,
            "id": full_name,
            "url": None,
            "source_type": "physical_table",
            "verified": False,
            "raw_sql_table_name": raw,
        }
        view_block_cache[view_name] = result
        return result

    # Pattern 2: derived_table with sql
    derived_sql_m = re.search(r'derived_table:\s*\{', block)
    if derived_sql_m:
        # Check for explore_source (Pattern 3) first
        explore_source_m = re.search(r'explore_source:\s*(\w+)', block)
        if explore_source_m:
            result = {
                "type": "explore",
                "name": explore_source_m.group(1),
                "id": explore_source_m.group(1),
                "url": None,
                "source_type": "derived_table_ndt",
                "verified": False,
            }
            view_block_cache[view_name] = result
            return result

        # SQL-based derived table
        sql_m = re.search(r'sql:\s*(.*?)\s*;;', block, re.DOTALL)
        if sql_m:
            source_tables, view_refs = _extract_sql_tables(sql_m.group(1))
            entries = []
            for st in source_tables:
                entries.append({
                    "type": "table",
                    "name": st["full_name"],
                    "id": st["full_name"],
                    "url": None,
                    "source_type": "derived_table_sql",
                    "verified": False,
                })
            for vref in view_refs:
                ref_result = _resolve_view_table(
                    vref, project_root, constants, file_index, view_block_cache, depth + 1,
                )
                if ref_result:
                    ref_copy = dict(ref_result)
                    ref_copy["source_type"] = "derived_table_sql_view_ref"
                    entries.append(ref_copy)

            # Cache first entry (or a composite if multiple)
            if len(entries) == 1:
                view_block_cache[view_name] = entries[0]
                return entries[0]
            elif entries:
                # Return list marker for multi-table derived tables
                composite = {"_multi": True, "entries": entries}
                view_block_cache[view_name] = composite
                return composite
            view_block_cache[view_name] = None
            return None

    # Pattern 4: implicit — no sql_table_name, no derived_table
    schema = constants.get("schema", "unknown_schema")
    full_name = f"{schema}.{view_name}"
    logger.info("Implicit table name used for view '%s'. Schema defaulted to '%s'.", view_name, schema)
    result = {
        "type": "table",
        "name": full_name,
        "id": full_name,
        "url": None,
        "source_type": "implicit",
        "verified": False,
    }
    view_block_cache[view_name] = result
    return result


class _ResolutionStats:
    """Tracks statistics for the table resolution stage."""

    def __init__(self):
        self.fields_processed = 0
        self.physical_table = 0
        self.implicit = 0
        self.derived_sql = 0
        self.derived_sql_source_tables = 0
        self.derived_ndt = 0
        self.view_refs_resolved = 0
        self.view_refs_unresolved = 0
        self.constants_loaded = 0
        self.templated_warnings = 0
        self.no_view_file = 0
        self.project_imports = 0

    def log_summary(self):
        logger.info(
            "\nTable resolution summary (file-based parsing, no MCP connection)\n"
            "──────────────────────────────────────────────────────────────────\n"
            "Fields processed:              %d\n"
            "Resolved as physical_table:    %d\n"
            "Resolved as implicit:          %d\n"
            "Resolved as derived_sql:       %d  (with %d source tables total)\n"
            "Resolved as derived_ndt:       %d\n"
            "View references resolved:      %d\n"
            "View references unresolved:    %d\n"
            "Manifest constants loaded:     %d\n"
            "Project imports found:         %d\n"
            "No view file found:            %d\n"
            "──────────────────────────────────────────────────────────────────\n"
            "All verified: false (no database connection available)",
            self.fields_processed, self.physical_table, self.implicit,
            self.derived_sql, self.derived_sql_source_tables,
            self.derived_ndt, self.view_refs_resolved,
            self.view_refs_unresolved, self.constants_loaded,
            self.project_imports, self.no_view_file,
        )


def resolve_related_entries(
    terms: list[GlossaryTerm],
    project_root: str,
    file_index: _FileIndex | None = None,
    imported_roots: list[str] | None = None,
) -> None:
    """Resolve source table for each glossary term and add as related_entries.
    Mutates terms in-place."""
    constants, imports = _load_manifest(project_root)

    # Build file index covering the project root and any imported project roots
    extra_roots = list(imported_roots or [])
    if file_index is None:
        file_index = _FileIndex(project_root, extra_roots)

    view_block_cache: dict[str, dict] = {}
    stats = _ResolutionStats()
    stats.constants_loaded = len(constants)
    stats.project_imports = len(imports)

    for term in terms:
        stats.fields_processed += 1
        view_name = term.view_name
        if not view_name:
            continue

        result = _resolve_view_table(
            view_name, project_root, constants, file_index, view_block_cache,
        )
        if result is None:
            stats.no_view_file += 1
            continue

        # Handle multi-table derived tables
        if isinstance(result, dict) and result.get("_multi"):
            for entry in result["entries"]:
                src = entry.get("source_type", "")
                if src == "derived_table_sql":
                    stats.derived_sql_source_tables += 1
                elif src == "derived_table_sql_view_ref":
                    stats.view_refs_resolved += 1
                term.related_entries.append(entry)
            stats.derived_sql += 1
        else:
            src = result.get("source_type", "")
            if src == "physical_table":
                stats.physical_table += 1
            elif src == "implicit":
                stats.implicit += 1
            elif src == "derived_table_ndt":
                stats.derived_ndt += 1
            elif src == "derived_table_sql":
                stats.derived_sql += 1
            elif src == "unresolved":
                stats.view_refs_unresolved += 1
            term.related_entries.append(result)

    stats.log_summary()


# ---------------------------------------------------------------------------
# Public API: run all enrichments
# ---------------------------------------------------------------------------


def enrich_terms(
    terms: list[GlossaryTerm],
    project_root: str,
    imported_roots: list[str] | None = None,
) -> None:
    """Run all enrichment passes on the glossary terms. Mutates in-place."""
    find_synonyms(terms)
    find_related_terms(terms)
    # Build file index once and share across resolution
    extra_roots = list(imported_roots or [])
    file_index = _FileIndex(project_root, extra_roots)
    resolve_related_entries(terms, project_root, file_index, imported_roots)

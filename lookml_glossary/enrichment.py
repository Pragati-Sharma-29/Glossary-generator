"""Enrich glossary terms with synonyms, related terms, and related entries."""

import logging
import os
import re
from typing import Optional

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


def _tokenize(name: str) -> set[str]:
    """Split a name into a set of meaningful word tokens."""
    return set(re.split(r"[\s_]+", name.lower().strip())) - {"", "of", "the", "a", "an", "in", "for"}


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
# 1. Synonym detection
# ---------------------------------------------------------------------------

_SYNONYM_THRESHOLD = 0.7


def find_synonyms(terms: list[GlossaryTerm]) -> None:
    """Find synonym terms: fields across ALL explores with identical/near-identical
    labels OR same underlying view and column. Mutates terms in-place."""
    n = len(terms)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = terms[i], terms[j]
            # Skip self-references (same field_id)
            if a.field_id == b.field_id:
                continue
            is_synonym = False
            # Rule 1: identical or near-identical labels
            if _label_similarity(a.name, b.name) >= _SYNONYM_THRESHOLD:
                is_synonym = True
            # Rule 2: same underlying view and column (same sql on same view)
            if (a.view_name and a.view_name == b.view_name
                    and a.sql_expression and a.sql_expression == b.sql_expression
                    and a.field_id != b.field_id):
                is_synonym = True

            if is_synonym:
                syn_b = {"term_name": b.name, "field_id": b.field_id,
                         "view_name": b.view_name or "", "explore_name": b.explore_name or ""}
                syn_a = {"term_name": a.name, "field_id": a.field_id,
                         "view_name": a.view_name or "", "explore_name": a.explore_name or ""}
                # Avoid duplicates
                if syn_b not in a.synonyms:
                    a.synonyms.append(syn_b)
                if syn_a not in b.synonyms:
                    b.synonyms.append(syn_a)


# ---------------------------------------------------------------------------
# 2. Related terms detection
# ---------------------------------------------------------------------------

_MAX_RELATED = 5


def find_related_terms(terms: list[GlossaryTerm]) -> None:
    """Find related terms: measures/dimensions in the SAME explore that share
    the same view name or have complementary labels. Mutates terms in-place."""
    # Group terms by explore
    by_explore: dict[str, list[GlossaryTerm]] = {}
    for t in terms:
        key = t.explore_name or "__no_explore__"
        by_explore.setdefault(key, []).append(t)

    for explore_terms in by_explore.values():
        n = len(explore_terms)
        for i in range(n):
            a = explore_terms[i]
            candidates: list[tuple[float, GlossaryTerm]] = []
            for j in range(n):
                if i == j:
                    continue
                b = explore_terms[j]
                if a.field_id == b.field_id:
                    continue
                score = 0.0
                # Same view = strong relation
                if a.view_name and a.view_name == b.view_name:
                    score += 0.5
                # Semantic similarity of labels
                score += _label_similarity(a.name, b.name) * 0.5
                if score > 0.1:
                    candidates.append((score, b))

            # Sort by score descending, take top N
            candidates.sort(key=lambda x: x[0], reverse=True)
            for score, b in candidates[:_MAX_RELATED]:
                entry = {"term_name": b.name, "field_id": b.field_id,
                         "type": b.term_type, "view_name": b.view_name or ""}
                if entry not in a.related_terms:
                    a.related_terms.append(entry)


# ---------------------------------------------------------------------------
# 3. Related entries — source table resolution
# ---------------------------------------------------------------------------


def _load_manifest_constants(project_root: str) -> dict[str, str]:
    """Load constant declarations from manifest.lkml if present."""
    manifest_path = os.path.join(project_root, "manifest.lkml")
    constants: dict[str, str] = {}
    if not os.path.exists(manifest_path):
        logger.info("No manifest.lkml found. Templated sql_table_name values may not resolve correctly.")
        return constants
    try:
        with open(manifest_path, "r") as f:
            content = f.read()
        # Match constant: name { value: "..." }
        for m in re.finditer(r'constant:\s*(\w+)\s*\{\s*value:\s*"([^"]*)"', content):
            constants[m.group(1)] = m.group(2)
        logger.info("Loaded %d manifest constants.", len(constants))
    except Exception:
        logger.warning("Failed to read manifest.lkml.")
    return constants


def _is_safe_path(filepath: str, project_root: str) -> bool:
    """Verify a resolved path stays within the project root."""
    real = os.path.realpath(filepath)
    root = os.path.realpath(project_root)
    return real.startswith(root + os.sep) or real == root


def _find_view_file(view_name: str, project_root: str,
                    file_cache: dict[str, str]) -> Optional[str]:
    """Find the file containing a view definition.

    All paths are validated to stay within project_root and symlinks
    are not followed during directory walks.
    """
    # Priority 1: standard paths
    candidates = [
        os.path.join(project_root, "views", f"{view_name}.view.lkml"),
        os.path.join(project_root, f"{view_name}.view.lkml"),
        os.path.join(project_root, "models", f"{view_name}.view.lkml"),
    ]
    for path in candidates:
        if os.path.exists(path) and _is_safe_path(path, project_root):
            return path

    # Priority 2: recursive search for *.view.lkml containing the view
    target = f"view: {view_name} " + "{"
    for root, _, filenames in os.walk(project_root, followlinks=False):
        for fn in filenames:
            if fn.endswith(".view.lkml"):
                fpath = os.path.join(root, fn)
                if not _is_safe_path(fpath, project_root):
                    continue
                content = _read_cached(fpath, file_cache)
                if content and target in content:
                    return fpath

    # Priority 3: search model/explore files
    for root, _, filenames in os.walk(project_root, followlinks=False):
        for fn in filenames:
            if fn.endswith((".model.lkml", ".explore.lkml")):
                fpath = os.path.join(root, fn)
                if not _is_safe_path(fpath, project_root):
                    continue
                content = _read_cached(fpath, file_cache)
                if content and target in content:
                    return fpath

    return None


def _read_cached(filepath: str, file_cache: dict[str, str]) -> Optional[str]:
    """Read a file, using cache if available."""
    if filepath in file_cache:
        return file_cache[filepath]
    try:
        with open(filepath, "r") as f:
            content = f.read()
        file_cache[filepath] = content
        return content
    except Exception:
        return None


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


_MAX_SQL_LENGTH = 50000


def _extract_sql_tables(sql: str) -> tuple[list[dict], list[str]]:
    """Extract physical table refs and view references from derived_table SQL."""
    source_tables = []
    view_references = []

    # Guard against ReDoS on excessively large SQL
    if len(sql) > _MAX_SQL_LENGTH:
        logger.warning("SQL too large (%d chars) for table extraction; skipping.", len(sql))
        return source_tables, view_references

    # Find CTE aliases to exclude
    cte_pattern = re.compile(r'\bWITH\b(.*?)(?=\bSELECT\b)', re.IGNORECASE | re.DOTALL)
    cte_aliases = set()
    cte_match = cte_pattern.search(sql)
    if cte_match:
        for alias_m in re.finditer(r'(\w+)\s+AS\s*\(', cte_match.group(1), re.IGNORECASE):
            cte_aliases.add(alias_m.group(1).lower())

    # Find FROM/JOIN table references
    table_pattern = re.compile(
        r'(?:FROM|JOIN)\s+([^\s(,;]+)',
        re.IGNORECASE,
    )
    for m in table_pattern.finditer(sql):
        ref = m.group(1).strip().strip('`"')
        if ref.lower() in cte_aliases:
            continue
        # Looker view reference: ${view_name.SQL_TABLE_NAME}
        view_ref_m = re.match(r'\$\{(\w+)\.SQL_TABLE_NAME\}', ref, re.IGNORECASE)
        if view_ref_m:
            view_references.append(view_ref_m.group(1))
            continue
        if ref.startswith('('):
            continue
        # Parse as table name
        if '.' in ref:
            parts = ref.split('.', 1)
            source_tables.append({
                "full_name": ref,
                "schema": parts[0],
                "table": parts[1],
                "raw_value": ref,
            })
        else:
            source_tables.append({
                "full_name": ref,
                "schema": "",
                "table": ref,
                "raw_value": ref,
            })

    return source_tables, view_references


def _resolve_view_table(
    view_name: str,
    project_root: str,
    constants: dict[str, str],
    file_cache: dict[str, str],
    view_block_cache: dict[str, dict],
    depth: int = 0,
) -> Optional[dict]:
    """Resolve the source table for a view, with recursive resolution and caching."""
    if view_name in view_block_cache:
        return view_block_cache[view_name]

    if depth > 3:
        logger.warning("Recursion depth exceeded resolving view '%s'. Stopped at depth 3.", view_name)
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

    filepath = _find_view_file(view_name, project_root, file_cache)
    if not filepath:
        logger.warning("No view file found for view '%s' — table related_entry skipped.", view_name)
        view_block_cache[view_name] = None
        return None

    content = _read_cached(filepath, file_cache)
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
                    vref, project_root, constants, file_cache, view_block_cache, depth + 1,
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
        self.files_from_disk = 0
        self.files_from_cache = 0

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
            "No view file found:            %d\n"
            "──────────────────────────────────────────────────────────────────\n"
            "All verified: false (no database connection available)",
            self.fields_processed, self.physical_table, self.implicit,
            self.derived_sql, self.derived_sql_source_tables,
            self.derived_ndt, self.view_refs_resolved,
            self.view_refs_unresolved, self.constants_loaded,
            self.no_view_file,
        )


def resolve_related_entries(terms: list[GlossaryTerm], project_root: str) -> None:
    """Resolve source table for each glossary term and add as related_entries.
    Mutates terms in-place."""
    constants = _load_manifest_constants(project_root)
    file_cache: dict[str, str] = {}
    view_block_cache: dict[str, dict] = {}
    stats = _ResolutionStats()
    stats.constants_loaded = len(constants)

    for term in terms:
        stats.fields_processed += 1
        view_name = term.view_name
        if not view_name:
            continue

        result = _resolve_view_table(
            view_name, project_root, constants, file_cache, view_block_cache,
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


def enrich_terms(terms: list[GlossaryTerm], project_root: str) -> None:
    """Run all enrichment passes on the glossary terms. Mutates in-place."""
    find_synonyms(terms)
    find_related_terms(terms)
    resolve_related_entries(terms, project_root)

"""Parse LookML model files and extract glossary-relevant elements."""

import fnmatch
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

import lkml

logger = logging.getLogger(__name__)


@dataclass
class DashboardLink:
    """A link to a Looker dashboard."""

    title: str
    url: str


@dataclass
class GlossaryTerm:
    """A single glossary entry extracted from a LookML model."""

    name: str
    description: str
    term_type: str  # "dimension", "measure", "metric", "kpi", "explore", "view"
    sql_expression: Optional[str] = None
    table_name: Optional[str] = None
    view_name: Optional[str] = None
    explore_name: Optional[str] = None
    model_name: Optional[str] = None
    value_format: Optional[str] = None
    measure_type: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    dashboard_links: list[DashboardLink] = field(default_factory=list)
    recommended_links: list[DashboardLink] = field(default_factory=list)
    is_metric: bool = False
    is_kpi: bool = False
    synonyms: list[dict] = field(default_factory=list)
    related_terms: list[dict] = field(default_factory=list)
    related_entries: list[dict] = field(default_factory=list)
    field_id: str = ""  # "view_name.field_name" unique identifier
    is_dynamic_sql: bool = False  # True if SQL contains Liquid templates
    sql_branches: list[str] = field(default_factory=list)  # all possible SQL outputs


# Measure types that represent metrics / KPIs
METRIC_MEASURE_TYPES = {
    "sum", "count", "count_distinct", "average", "median",
    "min", "max", "sum_distinct", "average_distinct",
    "percentile", "percent_of_total", "running_total",
    "number",
}

KPI_TAGS = {"kpi", "key_metric", "key-metric", "key_performance_indicator"}


def _clean_label(name: str) -> str:
    """Convert a LookML identifier to a human-readable label."""
    return name.replace("_", " ").strip().title()


def _extract_table_name(view: dict) -> Optional[str]:
    """Extract the underlying table name from a view definition."""
    sql_table = view.get("sql_table_name")
    if sql_table:
        return sql_table.strip().rstrip(";").strip()
    derived = view.get("derived_table")
    if derived and "sql" in derived:
        return f"(derived table from {view['name']})"
    return None


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL comments to avoid leaking secrets embedded in comments."""
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)  # block comments
    sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)   # line comments
    return sql.strip()


def _extract_sql_and_branches(raw_sql: str) -> tuple[str, bool, list[str]]:
    """Process a raw SQL expression: strip comments, detect Liquid, extract branches.

    Returns (cleaned_sql, is_dynamic, branches).
    """
    from .liquid import has_liquid, extract_liquid_branches

    cleaned = _strip_sql_comments(raw_sql)
    if not has_liquid(cleaned):
        return cleaned, False, []

    branches = extract_liquid_branches(cleaned)
    # Strip comments from each branch individually
    branches = [_strip_sql_comments(b) for b in branches]
    branches = [b for b in branches if b]  # remove empties
    return cleaned, True, branches


_SAFE_URL_PREFIXES = ("http://", "https://", "/")


def _extract_links(dimension_or_measure: dict) -> list[DashboardLink]:
    """Extract link entries from a dimension or measure.

    Only allows http://, https://, and relative (/) URLs to prevent
    javascript: and data: XSS attacks.
    """
    links = []
    for link in dimension_or_measure.get("links", []):
        label = link.get("label", "Link")
        url = link.get("url", "")
        if url and url.lower().startswith(_SAFE_URL_PREFIXES):
            links.append(DashboardLink(title=label, url=url))
    return links


def _build_description(item: dict, fallback_name: str) -> str:
    """Build a description string from a LookML element."""
    desc = item.get("description", "")
    if desc:
        return desc.strip()
    label = item.get("label", "")
    if label:
        return label.strip()
    return f"Auto-generated from LookML field: {_clean_label(fallback_name)}"


def parse_lookml_file(filepath: str) -> dict:
    """Parse a single LookML file and return the raw parsed dictionary."""
    with open(filepath, "r") as f:
        return lkml.load(f.read())


def _enrich_description(
    base_desc: str,
    view_name: str,
    explore_name: str,
    explore_desc: str,
    table_name: Optional[str],
) -> str:
    """Enrich a term description with view and explore context."""
    parts = [base_desc]
    view_info = f"View: {_clean_label(view_name)}"
    if table_name:
        view_info += f" (table: {table_name})"
    parts.append(view_info)
    if explore_name:
        explore_info = f"Explore: {_clean_label(explore_name)}"
        if explore_desc:
            explore_info += f" — {explore_desc}"
        parts.append(explore_info)
    return " | ".join(parts)


def extract_terms_from_view(
    view: dict,
    model_name: str = "",
    explore_name: str = "",
    dashboard_map: dict[str, list[DashboardLink]] | None = None,
    explore_desc: str = "",
) -> list[GlossaryTerm]:
    """Extract glossary terms from a single LookML view.

    Only creates terms for dimensions, measures, metrics, and KPIs.
    View and explore context is captured in each term's description.
    """
    terms = []
    view_name = view.get("name", "unknown")
    table_name = _extract_table_name(view)
    dashboard_map = dashboard_map or {}

    # Dimensions
    for dim in view.get("dimensions", []):
        name = dim.get("name", "")
        sql_expr, is_dynamic, branches = _extract_sql_and_branches(dim.get("sql", ""))
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_enrich_description(
                _build_description(dim, name), view_name, explore_name, explore_desc, table_name,
            ),
            term_type="dimension",
            sql_expression=sql_expr,
            table_name=table_name,
            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            tags=dim.get("tags", []),
            recommended_links=_extract_links(dim),
            field_id=f"{view_name}.{name}",
            is_dynamic_sql=is_dynamic,
            sql_branches=branches,
        ))

    # Dimension groups
    for dg in view.get("dimension_groups", []):
        name = dg.get("name", "")
        sql_expr, is_dynamic, branches = _extract_sql_and_branches(dg.get("sql", ""))
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_enrich_description(
                _build_description(dg, name), view_name, explore_name, explore_desc, table_name,
            ),
            term_type="dimension",
            sql_expression=sql_expr,
            table_name=table_name,
            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            tags=dg.get("tags", []),
            field_id=f"{view_name}.{name}",
            is_dynamic_sql=is_dynamic,
            sql_branches=branches,
        ))

    # Measures (metrics & KPIs)
    for measure in view.get("measures", []):
        name = measure.get("name", "")
        mtype = measure.get("type", "").lower()
        tags = [t.lower() for t in measure.get("tags", [])]

        is_metric = mtype in METRIC_MEASURE_TYPES
        is_kpi = bool(set(tags) & KPI_TAGS)

        term_type = "measure"

        # Attach dashboard links from dashboard_map if available
        field_ref = f"{view_name}.{name}"
        dash_links = dashboard_map.get(field_ref, [])

        sql_expr, is_dynamic, branches = _extract_sql_and_branches(measure.get("sql", ""))
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_enrich_description(
                _build_description(measure, name), view_name, explore_name, explore_desc, table_name,
            ),
            term_type=term_type,
            sql_expression=sql_expr,
            table_name=table_name,
            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            value_format=measure.get("value_format_name", measure.get("value_format", "")),
            measure_type=mtype,
            tags=measure.get("tags", []),
            is_metric=is_metric,
            is_kpi=is_kpi,
            dashboard_links=dash_links,
            recommended_links=_extract_links(measure),
            field_id=f"{view_name}.{name}",
            is_dynamic_sql=is_dynamic,
            sql_branches=branches,
        ))

    return terms


def extract_dashboard_links(parsed: dict) -> dict[str, list[DashboardLink]]:
    """Build a map of field_ref -> dashboard links from dashboard files."""
    dashboard_map: dict[str, list[DashboardLink]] = {}
    for dashboard in parsed.get("dashboards", []):
        dash_title = dashboard.get("title", dashboard.get("name", "Dashboard"))
        dash_name = dashboard.get("name", "")
        dash_link = DashboardLink(
            title=dash_title,
            url=f"/dashboards/{dash_name}" if dash_name else "",
        )
        for element in dashboard.get("elements", []):
            for field_ref in element.get("fields", []):
                dashboard_map.setdefault(field_ref, []).append(dash_link)
            # Also check measures and dimensions lists
            for field_ref in element.get("measures", []):
                dashboard_map.setdefault(field_ref, []).append(dash_link)
            for field_ref in element.get("dimensions", []):
                dashboard_map.setdefault(field_ref, []).append(dash_link)
    return dashboard_map


def _resolve_imported_project_roots(project_root: str) -> list[str]:
    """Parse manifest.lkml for local_dependency declarations and return their paths.

    Remote dependencies are logged but cannot be resolved from the filesystem.
    """
    manifest_path = os.path.join(project_root, "manifest.lkml")
    roots: list[str] = []
    if not os.path.exists(manifest_path):
        return roots
    try:
        with open(manifest_path, "r") as f:
            content = f.read()
        for m in re.finditer(
            r'local_dependency:\s*\{([^}]*)\}', content, re.DOTALL
        ):
            body = m.group(1)
            project_m = re.search(r'project:\s*"([^"]*)"', body)
            if project_m:
                # Local dependencies are sibling directories by convention
                dep_path = os.path.normpath(os.path.join(project_root, "..", project_m.group(1)))
                if os.path.isdir(dep_path):
                    roots.append(os.path.realpath(dep_path))
                    logger.info("Resolved local_dependency '%s' -> %s", project_m.group(1), dep_path)
                else:
                    logger.warning("local_dependency '%s' not found at %s", project_m.group(1), dep_path)
        for m in re.finditer(
            r'remote_dependency:\s*(\w+)\s*\{([^}]*)\}', content, re.DOTALL
        ):
            url_m = re.search(r'url:\s*"([^"]*)"', m.group(2))
            url = url_m.group(1) if url_m else "unknown"
            logger.info("Remote dependency '%s' (%s) cannot be resolved from filesystem. "
                        "Clone it locally and pass via -I flag.", m.group(1), url)
    except Exception:
        pass
    return roots


def parse_lookml_model(
    model_path: str,
    include_paths: list[str] | None = None,
) -> list[GlossaryTerm]:
    """Parse a LookML model file and all its included view/dashboard files.

    Args:
        model_path: Path to the .model.lkml file.
        include_paths: Additional directories to search for included files.

    Returns:
        A list of GlossaryTerm objects.
    """
    model_dir = os.path.dirname(os.path.abspath(model_path))
    imported_roots = _resolve_imported_project_roots(model_dir)
    search_dirs = [model_dir] + (include_paths or []) + imported_roots
    model_name = os.path.basename(model_path).replace(".model.lkml", "").replace(".lkml", "")

    parsed = parse_lookml_file(model_path)

    # Collect all LookML files to parse (includes)
    files_to_parse: list[str] = []
    seen_files: set[str] = set()
    safe_roots = [os.path.realpath(d) for d in search_dirs]
    for inc in parsed.get("includes", []):
        pattern = inc.replace("//", "/")
        for search_dir in search_dirs:
            for root, _, filenames in os.walk(search_dir, followlinks=False):
                for fn in filenames:
                    if not fn.endswith(".lkml"):
                        continue
                    fpath = os.path.join(root, fn)
                    if _matches_include(fpath, pattern, search_dir):
                        resolved = os.path.realpath(fpath)
                        if resolved in seen_files:
                            continue
                        if any(resolved.startswith(sr + os.sep) or resolved == sr
                               for sr in safe_roots):
                            files_to_parse.append(resolved)
                            seen_files.add(resolved)

    # --- Parallel file parsing stage ---
    # Parse all included files in parallel threads (I/O-bound).
    dashboard_files = [f for f in files_to_parse
                       if ".dashboard." in f or f.endswith(".dashboard.lkml")]
    view_files = [f for f in files_to_parse if f not in set(dashboard_files)]

    # Parse files in parallel
    parsed_dashboards: dict[str, dict] = {}
    parsed_views: dict[str, dict] = {}

    def _safe_parse(fpath: str) -> tuple[str, dict | None]:
        try:
            return fpath, parse_lookml_file(fpath)
        except Exception:
            return fpath, None

    all_parse_files = dashboard_files + view_files
    if all_parse_files:
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(_safe_parse, fp): fp for fp in all_parse_files}
            for future in as_completed(futures):
                fpath, result = future.result()
                if result is None:
                    continue
                if fpath in set(dashboard_files):
                    parsed_dashboards[fpath] = result
                else:
                    parsed_views[fpath] = result

    # Build dashboard link map from parsed dashboards
    dashboard_map: dict[str, list[DashboardLink]] = {}
    for dash_parsed in parsed_dashboards.values():
        dashboard_map.update(extract_dashboard_links(dash_parsed))

    # Build explore name and description maps from model
    explore_view_map: dict[str, str] = {}
    explore_desc_map: dict[str, str] = {}
    for explore in parsed.get("explores", []):
        exp_name = explore.get("name", "")
        from_view = explore.get("from", exp_name)
        explore_view_map[from_view] = exp_name
        explore_desc_map[exp_name] = explore.get("description", "")
        # Also map joined views
        for join in explore.get("joins", []):
            join_name = join.get("from", join.get("name", ""))
            if join_name:
                explore_view_map[join_name] = exp_name

    # Extract terms from views in the model file itself
    all_terms: list[GlossaryTerm] = []
    for view in parsed.get("views", []):
        vname = view.get("name", "")
        exp = explore_view_map.get(vname, "")
        all_terms.extend(extract_terms_from_view(
            view, model_name, exp, dashboard_map, explore_desc_map.get(exp, ""),
        ))

    # Extract terms from pre-parsed included view files
    for inc_parsed in parsed_views.values():
        for view in inc_parsed.get("views", []):
            vname = view.get("name", "")
            exp = explore_view_map.get(vname, "")
            all_terms.extend(extract_terms_from_view(
                view, model_name, exp, dashboard_map, explore_desc_map.get(exp, ""),
            ))

    # Enrich terms with synonyms, related terms, and related entries
    from .enrichment import enrich_terms
    enrich_terms(all_terms, model_dir, imported_roots)

    return all_terms


def _matches_include(filepath: str, pattern: str, search_dir: str) -> bool:
    """Check if a file path matches a LookML include pattern.

    Supports full glob patterns including:
      - *.view.lkml            (all view files in the search dir)
      - **/*.view.lkml         (all view files recursively)
      - views/subfolder/*.lkml (subdirectory patterns)
      - specific_file.view.lkml (exact match)
    """
    pattern = pattern.strip().lstrip("/")

    # Compute the relative path from the search directory
    try:
        rel_path = os.path.relpath(filepath, search_dir)
    except ValueError:
        # On Windows, relpath can fail across drives
        return False

    # Normalize to forward slashes for consistent matching
    rel_path = PurePosixPath(rel_path).as_posix()

    # Exact match (filename only or relative path)
    if rel_path == pattern or os.path.basename(filepath) == pattern:
        return True

    # Glob match against relative path
    if fnmatch.fnmatch(rel_path, pattern):
        return True

    # For patterns without path separators, match against basename only
    if "/" not in pattern and fnmatch.fnmatch(os.path.basename(filepath), pattern):
        return True

    return False

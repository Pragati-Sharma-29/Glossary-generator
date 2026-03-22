"""Parse LookML model files and extract glossary-relevant elements."""

import os
from dataclasses import dataclass, field
from typing import Optional

import lkml


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


def _extract_links(dimension_or_measure: dict) -> list[DashboardLink]:
    """Extract link entries from a dimension or measure."""
    links = []
    for link in dimension_or_measure.get("links", []):
        label = link.get("label", "Link")
        url = link.get("url", "")
        if url:
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
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_enrich_description(
                _build_description(dim, name), view_name, explore_name, explore_desc, table_name,
            ),
            term_type="dimension",
            sql_expression=dim.get("sql", ""),
            table_name=table_name,
            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            tags=dim.get("tags", []),
            recommended_links=_extract_links(dim),
            field_id=f"{view_name}.{name}",
        ))

    # Dimension groups
    for dg in view.get("dimension_groups", []):
        name = dg.get("name", "")
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_enrich_description(
                _build_description(dg, name), view_name, explore_name, explore_desc, table_name,
            ),
            term_type="dimension",
            sql_expression=dg.get("sql", ""),
            table_name=table_name,
            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            tags=dg.get("tags", []),
            field_id=f"{view_name}.{name}",
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

        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_enrich_description(
                _build_description(measure, name), view_name, explore_name, explore_desc, table_name,
            ),
            term_type=term_type,
            sql_expression=measure.get("sql", ""),
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
    search_dirs = [model_dir] + (include_paths or [])
    model_name = os.path.basename(model_path).replace(".model.lkml", "").replace(".lkml", "")

    parsed = parse_lookml_file(model_path)

    # Collect all LookML files to parse (includes)
    files_to_parse = []
    for inc in parsed.get("includes", []):
        pattern = inc.replace("//", "/")
        for search_dir in search_dirs:
            for root, _, filenames in os.walk(search_dir):
                for fn in filenames:
                    if fn.endswith(".lkml") and _matches_include(fn, pattern):
                        files_to_parse.append(os.path.join(root, fn))

    # Parse dashboards first to build the link map
    dashboard_map: dict[str, list[DashboardLink]] = {}
    for fpath in files_to_parse:
        if ".dashboard." in fpath or fpath.endswith(".dashboard.lkml"):
            try:
                dash_parsed = parse_lookml_file(fpath)
                dashboard_map.update(extract_dashboard_links(dash_parsed))
            except Exception:
                pass

    # Extract terms from views in the model file itself
    all_terms: list[GlossaryTerm] = []

    # Build explore name and description maps from model
    explore_view_map: dict[str, str] = {}
    explore_desc_map: dict[str, str] = {}
    for explore in parsed.get("explores", []):
        exp_name = explore.get("name", "")
        from_view = explore.get("from", exp_name)
        explore_view_map[from_view] = exp_name
        explore_desc_map[exp_name] = explore.get("description", "")

    # Views in the model file
    for view in parsed.get("views", []):
        vname = view.get("name", "")
        exp = explore_view_map.get(vname, "")
        all_terms.extend(extract_terms_from_view(
            view, model_name, exp, dashboard_map, explore_desc_map.get(exp, ""),
        ))

    # Views in included files
    for fpath in files_to_parse:
        if ".dashboard." in fpath:
            continue
        try:
            inc_parsed = parse_lookml_file(fpath)
            for view in inc_parsed.get("views", []):
                vname = view.get("name", "")
                exp = explore_view_map.get(vname, "")
                all_terms.extend(extract_terms_from_view(
                    view, model_name, exp, dashboard_map, explore_desc_map.get(exp, ""),
                ))
        except Exception:
            pass

    # Enrich terms with synonyms, related terms, and related entries
    from .enrichment import enrich_terms
    enrich_terms(all_terms, model_dir)

    return all_terms


def _matches_include(filename: str, pattern: str) -> bool:
    """Simple check if a filename matches a LookML include pattern."""
    pattern = pattern.strip().lstrip("/")
    if "*" in pattern:
        # Handle *.view.lkml, *.lkml, etc.
        suffix = pattern.split("*")[-1]
        return filename.endswith(suffix)
    return filename == pattern or filename == os.path.basename(pattern)

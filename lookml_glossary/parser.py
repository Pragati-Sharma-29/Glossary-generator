"""Parse LookML model files and extract glossary-relevant elements."""

import fnmatch
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

import yaml

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
    term_type: str  # "dimension", "measure", "parameter"
    sql_expression: Optional[str] = None
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
    is_hidden: bool = False
    synonyms: list[dict] = field(default_factory=list)
    related_terms: list[dict] = field(default_factory=list)
    related_entries: list[dict] = field(default_factory=list)
    aspects: list[dict] = field(default_factory=list)  # structured key-value metadata
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
    """Build a description string from a LookML element.

    Returns the explicit description or label if present, empty string otherwise.
    The caller is responsible for enriching with context via _enrich_description.
    """
    desc = item.get("description", "")
    if desc:
        return desc.strip()
    label = item.get("label", "")
    if label:
        return label.strip()
    return ""


def parse_lookml_file(filepath: str) -> dict:
    """Parse a single LookML file and return the raw parsed dictionary."""
    with open(filepath, "r") as f:
        return lkml.load(f.read())


def _generate_nl_description(
    name: str,
    term_type: str,
    measure_type: str,
    sql_expr: str,
    view_name: str,
    explore_name: str,
    explore_desc: str,
) -> str:
    """Generate a natural-language description from LookML context.

    Synthesises a readable sentence from the field name, type, SQL expression,
    and explore context when no explicit ``description`` is provided.
    """
    label = _clean_label(name)
    view_label = _clean_label(view_name)

    # Derive the column being referenced from the SQL expression
    col_hint = ""
    if sql_expr:
        # ${TABLE}.column_name → "column_name"
        m = re.search(r'\$\{TABLE\}\.(\w+)', sql_expr)
        if m:
            col_hint = _clean_label(m.group(1))
        else:
            # ${view_name.field} → "field from view"
            m = re.search(r'\$\{(\w+)\.(\w+)\}', sql_expr)
            if m:
                col_hint = f"{_clean_label(m.group(2))} from {_clean_label(m.group(1))}"

    if term_type == "measure":
        agg = measure_type or "aggregate"
        agg_labels = {
            "sum": "Total", "count": "Count of", "count_distinct": "Distinct count of",
            "average": "Average", "median": "Median", "min": "Minimum",
            "max": "Maximum", "sum_distinct": "Distinct sum of",
            "percent_of_total": "Percentage of total",
            "running_total": "Running total of",
            "number": "Calculated",
        }
        prefix = agg_labels.get(agg, agg.replace("_", " ").title() + " of")
        subject = col_hint or label
        # Avoid redundancy: "Average average quantity" → "Average quantity"
        # Also handles "Distinct count of count devices" → "Distinct count of devices"
        subject_lower = subject.lower()
        # Build list of words to strip from the start of the subject
        agg_synonyms = {
            "minimum": ["min", "minimum"], "maximum": ["max", "maximum"],
            "average": ["avg", "average"], "total": ["total", "sum"],
            "count": ["count", "cnt"], "median": ["median"],
            "calculated": ["calc", "calculated"],
            "distinct": ["distinct"],
        }
        check_words: list[str] = []
        for pword in prefix.lower().split():
            check_words.extend(agg_synonyms.get(pword, [pword]))
        for w in check_words:
            if subject_lower.startswith(w):
                trimmed = subject[len(w):].lstrip(" _")
                if trimmed:
                    subject = trimmed
                    subject_lower = subject.lower()
                    # Don't break — continue stripping ("count distinct" fields)
        nl = f"{prefix} {subject.lower()} in {view_label}"
    elif term_type == "parameter":
        nl = f"User-selectable parameter {label.lower()} for {view_label}"
    else:
        # dimension
        if col_hint:
            nl = f"{col_hint} attribute of {view_label}"
        else:
            nl = f"{label} attribute of {view_label}"

    if explore_desc:
        nl += f". {explore_desc.rstrip('.')}"
    elif explore_name:
        nl += f", used in the {_clean_label(explore_name)} explore"

    return nl


def _enrich_description(
    base_desc: str,
    view_name: str,
    explore_name: str,
    explore_desc: str,
    joins: list[dict] | None = None,
    *,
    nl_desc: str = "",
) -> str:
    """Enrich a term description with view/explore context and joins.

    Structure:  ``<base_or_nl> | View: … | Explore: … | Joins: …``

    *base_desc* is the explicit LookML description (may be empty).
    *nl_desc* is a generated natural-language fallback used when *base_desc*
    is empty.
    """
    # Use the explicit description when available, otherwise NL fallback
    primary = base_desc if base_desc else nl_desc
    parts = [primary] if primary else []

    parts.append(f"View: {_clean_label(view_name)}")

    if explore_name:
        explore_info = f"Explore: {_clean_label(explore_name)}"
        if explore_desc:
            explore_info += f" — {explore_desc}"
        parts.append(explore_info)

    if joins:
        join_parts: list[str] = []
        for j in joins:
            j_name = j.get("from", j.get("name", ""))
            j_rel = j.get("relationship", "")
            if j_name:
                entry = _clean_label(j_name)
                if j_rel:
                    entry += f" ({j_rel})"
                join_parts.append(entry)
        if join_parts:
            parts.append(f"Joins: {', '.join(join_parts)}")

    return " | ".join(parts)


def _build_aspects(item: dict, extra: dict | None = None) -> list[dict]:
    """Build a list of aspect dicts from a LookML dimension/measure/parameter.

    Aspects capture structured metadata that isn't a plain-text description:
    group_label, drill_fields, primary_key status, hidden status, etc.
    """
    aspects: list[dict] = []
    if item.get("group_label"):
        aspects.append({"key": "group_label", "value": item["group_label"]})
    if item.get("label"):
        aspects.append({"key": "label", "value": item["label"]})
    if item.get("primary_key") == "yes" or item.get("primary_key") is True:
        aspects.append({"key": "primary_key", "value": True})
    if item.get("drill_fields"):
        aspects.append({"key": "drill_fields", "value": item["drill_fields"]})
    if item.get("filters"):
        aspects.append({"key": "filters", "value": item["filters"]})
    if item.get("action") or item.get("actions"):
        actions = item.get("actions", [])
        if item.get("action"):
            actions = [item["action"]] if isinstance(item["action"], dict) else item.get("action", [])
        if actions:
            aspects.append({"key": "actions", "value": [
                {"label": a.get("label", ""), "url": a.get("url", "")} for a in actions
            ]})
    if extra:
        for k, v in extra.items():
            aspects.append({"key": k, "value": v})
    return aspects


def extract_terms_from_view(
    view: dict,
    model_name: str = "",
    explore_name: str = "",
    dashboard_map: dict[str, list[DashboardLink]] | None = None,
    explore_desc: str = "",
    explore_joins: list[dict] | None = None,
) -> list[GlossaryTerm]:
    """Extract glossary terms from a single LookML view.

    Creates terms for dimensions, dimension group timeframes, measures,
    and parameters. View and explore context is captured in each term's
    description.
    """
    terms = []
    view_name = view.get("name", "unknown")
    dashboard_map = dashboard_map or {}
    joins = explore_joins or []

    def _desc(item: dict, name: str, term_type: str = "dimension",
              measure_type: str = "", sql_expr: str = "") -> str:
        base = _build_description(item, name)
        nl = _generate_nl_description(
            name, term_type, measure_type, sql_expr,
            view_name, explore_name, explore_desc,
        )
        return _enrich_description(
            base, view_name, explore_name, explore_desc,
            joins, nl_desc=nl,
        )

    # Dimensions
    for dim in view.get("dimensions", []):
        name = dim.get("name", "")
        sql_expr, is_dynamic, branches = _extract_sql_and_branches(dim.get("sql", ""))
        hidden = dim.get("hidden") in ("yes", True)
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_desc(dim, name, "dimension", sql_expr=sql_expr),
            term_type="dimension",
            sql_expression=sql_expr,

            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            tags=dim.get("tags", []),
            recommended_links=_extract_links(dim),
            is_hidden=hidden,
            aspects=_build_aspects(dim),
            field_id=f"{view_name}.{name}",
            is_dynamic_sql=is_dynamic,
            sql_branches=branches,
        ))

    # Dimension groups — expand into individual timeframe terms
    for dg in view.get("dimension_groups", []):
        base_name = dg.get("name", "")
        sql_expr, is_dynamic, branches = _extract_sql_and_branches(dg.get("sql", ""))
        hidden = dg.get("hidden") in ("yes", True)
        dg_type = dg.get("type", "time")
        timeframes = dg.get("timeframes", [])

        if timeframes:
            for tf in timeframes:
                tf_name = f"{base_name}_{tf}"
                tf_label = _clean_label(tf_name)
                extra_aspects = {"dimension_group": base_name, "timeframe": tf, "dimension_group_type": dg_type}
                terms.append(GlossaryTerm(
                    name=tf_label,
                    description=_desc(dg, tf_name, "dimension", sql_expr=sql_expr),
                    term_type="dimension",
                    sql_expression=sql_expr,
        
                    view_name=view_name,
                    explore_name=explore_name,
                    model_name=model_name,
                    tags=dg.get("tags", []),
                    is_hidden=hidden,
                    aspects=_build_aspects(dg, extra_aspects),
                    field_id=f"{view_name}.{tf_name}",
                    is_dynamic_sql=is_dynamic,
                    sql_branches=branches,
                ))
        else:
            # No timeframes listed — emit the base group name
            terms.append(GlossaryTerm(
                name=_clean_label(base_name),
                description=_desc(dg, base_name, "dimension", sql_expr=sql_expr),
                term_type="dimension",
                sql_expression=sql_expr,
    
                view_name=view_name,
                explore_name=explore_name,
                model_name=model_name,
                tags=dg.get("tags", []),
                is_hidden=hidden,
                aspects=_build_aspects(dg, {"dimension_group_type": dg_type}),
                field_id=f"{view_name}.{base_name}",
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
        hidden = measure.get("hidden") in ("yes", True)

        # Attach dashboard links from dashboard_map if available
        field_ref = f"{view_name}.{name}"
        dash_links = dashboard_map.get(field_ref, [])

        sql_expr, is_dynamic, branches = _extract_sql_and_branches(measure.get("sql", ""))
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_desc(measure, name, "measure", mtype, sql_expr),
            term_type="measure",
            sql_expression=sql_expr,

            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            value_format=measure.get("value_format_name", measure.get("value_format", "")),
            measure_type=mtype,
            tags=measure.get("tags", []),
            is_metric=is_metric,
            is_kpi=is_kpi,
            is_hidden=hidden,
            dashboard_links=dash_links,
            recommended_links=_extract_links(measure),
            aspects=_build_aspects(measure),
            field_id=f"{view_name}.{name}",
            is_dynamic_sql=is_dynamic,
            sql_branches=branches,
        ))

    # Parameters
    for param in view.get("parameters", []):
        name = param.get("name", "")
        ptype = param.get("type", "unfiltered")
        hidden = param.get("hidden") in ("yes", True)
        default_val = param.get("default_value", "")
        allowed = param.get("allowed_values", [])
        param_aspects = [
            {"key": "parameter_type", "value": ptype},
        ]
        if default_val:
            param_aspects.append({"key": "default_value", "value": default_val})
        if allowed:
            param_aspects.append({"key": "allowed_values", "value": allowed})
        if param.get("label"):
            param_aspects.append({"key": "label", "value": param["label"]})

        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_desc(param, name, "parameter"),
            term_type="parameter",

            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            is_hidden=hidden,
            aspects=param_aspects,
            field_id=f"{view_name}.{name}",
        ))

    # Filters (templated filter fields)
    for filt in view.get("filters", []):
        name = filt.get("name", "")
        ftype = filt.get("type", "string")
        hidden = filt.get("hidden") in ("yes", True)
        filt_aspects = [{"key": "filter_type", "value": ftype}]
        if filt.get("default_value"):
            filt_aspects.append({"key": "default_value", "value": filt["default_value"]})
        if filt.get("label"):
            filt_aspects.append({"key": "label", "value": filt["label"]})

        sql_expr, is_dynamic, branches = _extract_sql_and_branches(filt.get("sql", ""))
        terms.append(GlossaryTerm(
            name=_clean_label(name),
            description=_desc(filt, name, "parameter", sql_expr=sql_expr),
            term_type="parameter",
            sql_expression=sql_expr,

            view_name=view_name,
            explore_name=explore_name,
            model_name=model_name,
            is_hidden=hidden,
            aspects=filt_aspects,
            field_id=f"{view_name}.{name}",
            is_dynamic_sql=is_dynamic,
            sql_branches=branches,
        ))

    return terms


def _parse_yaml_dashboard(fpath: str) -> dict:
    """Parse a YAML-format .dashboard.lookml file into the same structure
    that ``extract_dashboard_links`` expects.

    YAML dashboards (used by many Looker projects) look like::

        - dashboard: my_dashboard
          title: My Dashboard
          elements:
          - title: Element Title
            model: my_model
            explore: my_explore
            fields: [view.dim1, view.measure1]

    Returns a dict with a ``dashboards`` key containing normalised dashboard
    dicts compatible with the LookML-parsed format.
    """
    try:
        with open(fpath, "r") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        logger.warning("Failed to parse YAML dashboard %s: %s", fpath, exc)
        return {"dashboards": []}

    if data is None:
        return {"dashboards": []}

    # YAML dashboards are a list of mappings, each with a 'dashboard' key
    items = data if isinstance(data, list) else [data]
    dashboards: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dash_name = item.get("dashboard", "")
        if not dash_name:
            continue
        dash_title = item.get("title", str(dash_name))
        elements: list[dict] = []
        for elem in item.get("elements", []):
            if not isinstance(elem, dict):
                continue
            # YAML dashboards put all field refs under 'fields'
            fields = elem.get("fields", []) or []
            if isinstance(fields, str):
                fields = [fields]
            elements.append({"fields": fields})
        dashboards.append({
            "name": str(dash_name),
            "title": dash_title,
            "elements": elements,
        })
    return {"dashboards": dashboards}


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
                    if not (fn.endswith(".lkml") or fn.endswith(".lookml")):
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
                       if ".dashboard." in f]
    view_files = [f for f in files_to_parse if f not in set(dashboard_files)]

    # Parse files in parallel
    parsed_dashboards: dict[str, dict] = {}
    parsed_views: dict[str, dict] = {}

    def _safe_parse(fpath: str) -> tuple[str, dict | None]:
        try:
            # .dashboard.lookml files use YAML format, not LookML syntax
            if fpath.endswith(".dashboard.lookml"):
                return fpath, _parse_yaml_dashboard(fpath)
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

    # Build explore name and description maps from model, resolving extends
    explore_view_map: dict[str, str] = {}
    explore_desc_map: dict[str, str] = {}

    # Also collect explores from parsed included files (for shared .explore.lkml files)
    all_explores_by_name: dict[str, dict] = {}
    for explore in parsed.get("explores", []):
        all_explores_by_name[explore.get("name", "")] = explore
    for inc_parsed in parsed_views.values():
        for explore in inc_parsed.get("explores", []):
            name = explore.get("name", "")
            if name and name not in all_explores_by_name:
                all_explores_by_name[name] = explore

    def _resolve_explore_joins(exp_name: str, seen: set[str] | None = None) -> list[dict]:
        """Recursively collect all joins for an explore, following extends chains."""
        if seen is None:
            seen = set()
        if exp_name in seen:
            return []
        seen.add(exp_name)
        explore = all_explores_by_name.get(exp_name)
        if not explore:
            return []
        joins = list(explore.get("joins", []))
        for parent_name in explore.get("extends", []):
            if isinstance(parent_name, str):
                joins.extend(_resolve_explore_joins(parent_name, seen))
            elif isinstance(parent_name, list):
                for pn in parent_name:
                    joins.extend(_resolve_explore_joins(pn, seen))
        # extends__all is how lkml parses the extends list
        for parent_name in explore.get("extends__all", []):
            if isinstance(parent_name, dict):
                for pn in parent_name.get("extends", []):
                    joins.extend(_resolve_explore_joins(pn, seen))
            elif isinstance(parent_name, str):
                joins.extend(_resolve_explore_joins(parent_name, seen))
        return joins

    explore_joins_map: dict[str, list[dict]] = {}

    for explore in all_explores_by_name.values():
        exp_name = explore.get("name", "")
        from_view = explore.get("from", exp_name)
        explore_view_map[from_view] = exp_name
        explore_desc_map[exp_name] = explore.get("description", "")
        # Resolve all joins including from extends chains
        all_joins = _resolve_explore_joins(exp_name)
        # De-duplicate joins by view name
        seen_join_names: set[str] = set()
        unique_joins: list[dict] = []
        for join in all_joins:
            join_view = join.get("from", join.get("name", ""))
            if join_view and join_view not in seen_join_names:
                explore_view_map[join_view] = exp_name
                seen_join_names.add(join_view)
                unique_joins.append(join)
        explore_joins_map[exp_name] = unique_joins

    # Extract terms from views in the model file itself
    all_terms: list[GlossaryTerm] = []
    for view in parsed.get("views", []):
        vname = view.get("name", "")
        exp = explore_view_map.get(vname, "")
        all_terms.extend(extract_terms_from_view(
            view, model_name, exp, dashboard_map, explore_desc_map.get(exp, ""),
            explore_joins_map.get(exp, []),
        ))

    # Extract terms from pre-parsed included view files
    for inc_parsed in parsed_views.values():
        for view in inc_parsed.get("views", []):
            vname = view.get("name", "")
            exp = explore_view_map.get(vname, "")
            all_terms.extend(extract_terms_from_view(
                view, model_name, exp, dashboard_map, explore_desc_map.get(exp, ""),
                explore_joins_map.get(exp, []),
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

    # Cross-match .lkml patterns against .lookml files and vice versa
    if pattern.endswith(".lkml"):
        alt_pattern = pattern[:-5] + ".lookml"
    elif pattern.endswith(".lookml"):
        alt_pattern = pattern[:-7] + ".lkml"
    else:
        return False

    if fnmatch.fnmatch(rel_path, alt_pattern):
        return True
    if "/" not in alt_pattern and fnmatch.fnmatch(os.path.basename(filepath), alt_pattern):
        return True

    return False

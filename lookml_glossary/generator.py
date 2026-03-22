"""Generate glossary output in various formats from parsed LookML terms."""

import csv
import json
import os
from typing import TextIO

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .parser import GlossaryTerm


TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

CSV_COLUMNS = [
    "term_name", "description", "type",
    "table_name", "view_name", "explore_name", "model_name",
    "measure_type", "sql_expression", "value_format", "tags",
    "dashboard_links", "recommended_links",
    "synonyms", "related_terms", "related_entries",
]


def _term_to_dict(term: GlossaryTerm) -> dict:
    """Convert a GlossaryTerm to a serialisable dictionary."""
    entry = {
        "term_name": term.name,
        "description": term.description,
        "type": term.term_type,
    }
    if term.table_name:
        entry["table_name"] = term.table_name
    if term.view_name:
        entry["view_name"] = term.view_name
    if term.explore_name:
        entry["explore_name"] = term.explore_name
    if term.model_name:
        entry["model_name"] = term.model_name
    if term.sql_expression:
        entry["sql_expression"] = term.sql_expression
    if term.measure_type:
        entry["measure_type"] = term.measure_type
    if term.value_format:
        entry["value_format"] = term.value_format
    if term.tags:
        entry["tags"] = term.tags
    if term.is_metric:
        entry["is_metric"] = True
    if term.is_kpi:
        entry["is_kpi"] = True
    if term.dashboard_links:
        entry["dashboard_links"] = [
            {"title": l.title, "url": l.url} for l in term.dashboard_links
        ]
    if term.recommended_links:
        entry["recommended_links"] = [
            {"title": l.title, "url": l.url} for l in term.recommended_links
        ]
    if term.synonyms:
        entry["synonyms"] = term.synonyms
    if term.related_terms:
        entry["related_terms"] = term.related_terms
    if term.related_entries:
        entry["related_entries"] = term.related_entries
    return entry


def _format_links_for_csv(links: list[dict]) -> str:
    """Format a list of link dicts as a readable string for CSV."""
    return "; ".join(f"{l['title']} ({l['url']})" for l in links)


def _term_to_csv_row(term: GlossaryTerm) -> dict:
    """Convert a GlossaryTerm to a flat dictionary suitable for CSV."""
    d = _term_to_dict(term)
    return {
        "term_name": d.get("term_name", ""),
        "description": d.get("description", ""),
        "type": d.get("type", ""),
        "table_name": d.get("table_name", ""),
        "view_name": d.get("view_name", ""),
        "explore_name": d.get("explore_name", ""),
        "model_name": d.get("model_name", ""),
        "measure_type": d.get("measure_type", ""),
        "sql_expression": d.get("sql_expression", ""),
        "value_format": d.get("value_format", ""),
        "tags": "; ".join(d.get("tags", [])),
        "dashboard_links": _format_links_for_csv(d.get("dashboard_links", [])),
        "recommended_links": _format_links_for_csv(d.get("recommended_links", [])),
        "synonyms": "; ".join(s.get("term_name", "") for s in d.get("synonyms", [])),
        "related_terms": "; ".join(r.get("term_name", "") for r in d.get("related_terms", [])),
        "related_entries": "; ".join(r.get("name", "") for r in d.get("related_entries", [])),
    }


def generate_csv(terms: list[GlossaryTerm], output: TextIO) -> None:
    """Write glossary terms as CSV."""
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for term in terms:
        writer.writerow(_term_to_csv_row(term))


def generate_json(terms: list[GlossaryTerm], output: TextIO) -> None:
    """Write glossary terms as JSON."""
    data = {
        "glossary": [_term_to_dict(t) for t in terms],
        "summary": _build_summary(terms),
    }
    json.dump(data, output, indent=2)


def generate_markdown(terms: list[GlossaryTerm], output: TextIO) -> None:
    """Write glossary terms as Markdown."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape([]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("glossary.md.j2")
    grouped = _group_terms(terms)
    summary = _build_summary(terms)
    output.write(template.render(grouped=grouped, summary=summary))


def generate_html(terms: list[GlossaryTerm], output: TextIO) -> None:
    """Write glossary terms as an HTML page."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("glossary.html.j2")
    grouped = _group_terms(terms)
    summary = _build_summary(terms)
    output.write(template.render(grouped=grouped, summary=summary))


def generate_webapp(terms: list[GlossaryTerm], output: TextIO) -> None:
    """Write a full interactive HTML page with diagram, term table, and CSV download."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("webapp.html.j2")
    grouped = _group_terms(terms)
    summary = _build_summary(terms)
    hierarchy = _build_hierarchy(terms)
    all_terms_dicts = [_term_to_dict(t) for t in terms]
    csv_rows = [_term_to_csv_row(t) for t in terms]
    output.write(template.render(
        grouped=grouped,
        summary=summary,
        hierarchy=hierarchy,
        all_terms=all_terms_dicts,
        csv_columns=CSV_COLUMNS,
        csv_rows=csv_rows,
    ))


def _build_hierarchy(terms: list[GlossaryTerm]) -> list:
    """Build the model->explore->view->fields hierarchy for the diagram.

    Views and explores are derived from field-level metadata since dedicated
    view/explore terms no longer exist.
    """
    models: dict[str, dict] = {}
    for t in terms:
        model = t.model_name or "unknown"
        if model not in models:
            models[model] = {"name": model, "explores": {}, "views": {}}

        # Auto-create explore entries from field metadata
        if t.explore_name and t.explore_name not in models[model]["explores"]:
            models[model]["explores"][t.explore_name] = {
                "name": t.explore_name,
                "label": _clean_label(t.explore_name),
                "description": "",
            }

        # Auto-create view entries from field metadata
        vname = t.view_name or ""
        if vname and vname not in models[model]["views"]:
            models[model]["views"][vname] = {
                "name": vname,
                "label": _clean_label(vname),
                "table_name": t.table_name or "",
                "explore": t.explore_name or "",
                "dimensions": [],
                "measures": [],
            }

        # Add field to its view
        if vname and vname in models[model]["views"]:
            entry = {"name": t.name, "type": t.term_type}
            if t.term_type == "dimension":
                models[model]["views"][vname]["dimensions"].append(entry)
            elif t.term_type == "measure":
                models[model]["views"][vname]["measures"].append(entry)

    # Convert inner dicts to lists for the template
    result = []
    for m in models.values():
        result.append({
            "name": m["name"],
            "explores": list(m["explores"].values()),
            "views": list(m["views"].values()),
        })
    return result


def _clean_label(name: str) -> str:
    """Convert a LookML identifier to a human-readable label."""
    return name.replace("_", " ").strip().title()


def _group_terms(terms: list[GlossaryTerm]) -> dict[str, list[dict]]:
    """Group terms by type for templated output."""
    groups: dict[str, list[dict]] = {
        "dimensions": [],
        "measures": [],
    }
    for t in terms:
        d = _term_to_dict(t)
        if t.term_type == "dimension":
            groups["dimensions"].append(d)
        else:
            groups["measures"].append(d)
    return groups


def _build_summary(terms: list[GlossaryTerm]) -> dict:
    """Build a summary of the glossary contents."""
    return {
        "total_terms": len(terms),
        "dimensions": sum(1 for t in terms if t.term_type == "dimension"),
        "measures": sum(1 for t in terms if t.term_type == "measure"),
    }

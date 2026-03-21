"""Generate glossary output in various formats from parsed LookML terms."""

import json
import os
from typing import TextIO

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .parser import GlossaryTerm


TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


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
    return entry


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


def _group_terms(terms: list[GlossaryTerm]) -> dict[str, list[dict]]:
    """Group terms by type for templated output."""
    groups: dict[str, list[dict]] = {
        "explores": [],
        "views": [],
        "metrics": [],
        "kpis": [],
        "dimensions": [],
        "measures": [],
    }
    for t in terms:
        d = _term_to_dict(t)
        if t.term_type == "explore":
            groups["explores"].append(d)
        elif t.term_type == "view":
            groups["views"].append(d)
        elif t.term_type == "kpi":
            groups["kpis"].append(d)
        elif t.is_metric or t.term_type == "metric":
            groups["metrics"].append(d)
        elif t.term_type == "dimension":
            groups["dimensions"].append(d)
        else:
            groups["measures"].append(d)
    return groups


def _build_summary(terms: list[GlossaryTerm]) -> dict:
    """Build a summary of the glossary contents."""
    return {
        "total_terms": len(terms),
        "explores": sum(1 for t in terms if t.term_type == "explore"),
        "views": sum(1 for t in terms if t.term_type == "view"),
        "metrics": sum(1 for t in terms if t.is_metric),
        "kpis": sum(1 for t in terms if t.is_kpi),
        "dimensions": sum(1 for t in terms if t.term_type == "dimension"),
        "measures": sum(1 for t in terms if t.term_type in ("measure", "metric", "kpi")),
    }

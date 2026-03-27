"""Detect drift between a saved glossary snapshot and the current LookML source."""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from .parser import GlossaryTerm

logger = logging.getLogger(__name__)

SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


@dataclass
class DriftItem:
    """A single detected drift between snapshot and current LookML state."""

    category: str
    severity: str
    field_id: str
    message: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None


def _base_description(desc: str) -> str:
    """Strip enriched context (after first '|') to compare only the base description."""
    return desc.split("|")[0].strip()


def _term_to_index(terms: list[GlossaryTerm]) -> dict[str, GlossaryTerm]:
    """Index a list of GlossaryTerm objects by field_id."""
    index: dict[str, GlossaryTerm] = {}
    for t in terms:
        if t.field_id:
            index[t.field_id] = t
    return index


def _snapshot_to_index(entries: list[dict]) -> dict[str, dict]:
    """Index snapshot entries by field_id, with basic validation."""
    index: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fid = entry.get("field_id", "")
        if fid:
            index[fid] = entry
    return index


def load_snapshot(path: str) -> list[dict]:
    """Load a previously generated JSON glossary snapshot.

    Expects the format produced by ``generate_json``:
    ``{"glossary": [...], "summary": {...}}``.
    """
    with open(path, "r") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "glossary" not in data:
        raise ValueError("Snapshot JSON must contain a 'glossary' key with a list of term dicts.")
    glossary = data["glossary"]
    if not isinstance(glossary, list):
        raise ValueError("'glossary' must be a list.")
    return glossary


def validate(
    snapshot_entries: list[dict],
    current_terms: list[GlossaryTerm],
) -> list[DriftItem]:
    """Compare a snapshot glossary against freshly parsed terms.

    Returns a list of ``DriftItem`` objects sorted by severity then field_id.
    """
    old = _snapshot_to_index(snapshot_entries)
    new = _term_to_index(current_terms)
    items: list[DriftItem] = []

    old_ids = set(old)
    new_ids = set(new)

    # -- Removed fields -------------------------------------------------------
    for fid in sorted(old_ids - new_ids):
        items.append(DriftItem(
            category="removed_field",
            severity="error",
            field_id=fid,
            message=f"Field '{fid}' was removed from LookML.",
        ))

    # -- New fields ------------------------------------------------------------
    for fid in sorted(new_ids - old_ids):
        items.append(DriftItem(
            category="new_field",
            severity="warning",
            field_id=fid,
            message=f"Field '{fid}' is new and not in the glossary snapshot.",
        ))

    # -- Fields present in both ------------------------------------------------
    for fid in sorted(old_ids & new_ids):
        snap = old[fid]
        cur = new[fid]

        # SQL changed
        old_sql = (snap.get("sql_expression") or "").strip()
        new_sql = (cur.sql_expression or "").strip()
        if old_sql != new_sql:
            items.append(DriftItem(
                category="sql_changed",
                severity="warning",
                field_id=fid,
                message=f"SQL expression changed for '{fid}'.",
                old_value=old_sql,
                new_value=new_sql,
            ))

        # Type changed
        old_type = snap.get("type", "")
        new_type = cur.term_type or ""
        old_mtype = snap.get("measure_type", "")
        new_mtype = cur.measure_type or ""
        if old_type != new_type or old_mtype != new_mtype:
            items.append(DriftItem(
                category="type_changed",
                severity="error",
                field_id=fid,
                message=f"Type changed for '{fid}': {old_type}/{old_mtype} -> {new_type}/{new_mtype}.",
                old_value=f"{old_type}/{old_mtype}",
                new_value=f"{new_type}/{new_mtype}",
            ))

        # Table renamed — compare source tables from related_entries
        old_entries = snap.get("related_entries", [])
        old_table = old_entries[0].get("name", "") if old_entries else ""
        new_entries = cur.related_entries or []
        new_table = new_entries[0].get("name", "") if new_entries else ""
        if old_table != new_table:
            items.append(DriftItem(
                category="table_renamed",
                severity="warning",
                field_id=fid,
                message=f"Table changed for '{fid}': '{old_table}' -> '{new_table}'.",
                old_value=old_table,
                new_value=new_table,
            ))

        # KPI / metric reclassified
        old_metric = snap.get("is_metric", False)
        new_metric = cur.is_metric
        old_kpi = snap.get("is_kpi", False)
        new_kpi = cur.is_kpi
        if old_metric != new_metric or old_kpi != new_kpi:
            items.append(DriftItem(
                category="kpi_reclassified",
                severity="warning",
                field_id=fid,
                message=f"Metric/KPI classification changed for '{fid}'.",
                old_value=f"is_metric={old_metric}, is_kpi={old_kpi}",
                new_value=f"is_metric={new_metric}, is_kpi={new_kpi}",
            ))

        # Description changed (base only, ignoring enriched context)
        old_desc = _base_description(snap.get("description", ""))
        new_desc = _base_description(cur.description or "")
        if old_desc != new_desc:
            items.append(DriftItem(
                category="description_changed",
                severity="info",
                field_id=fid,
                message=f"Description changed for '{fid}'.",
                old_value=old_desc,
                new_value=new_desc,
            ))

        # Tags changed
        old_tags = sorted(snap.get("tags", []))
        new_tags = sorted(cur.tags or [])
        if old_tags != new_tags:
            items.append(DriftItem(
                category="tags_changed",
                severity="info",
                field_id=fid,
                message=f"Tags changed for '{fid}'.",
                old_value=", ".join(old_tags),
                new_value=", ".join(new_tags),
            ))

    # -- View-level and explore-level removals ---------------------------------
    old_views = {e.get("view_name", "") for e in snapshot_entries if e.get("view_name")}
    new_views = {t.view_name for t in current_terms if t.view_name}
    for vname in sorted(old_views - new_views):
        items.append(DriftItem(
            category="view_removed",
            severity="error",
            field_id=vname,
            message=f"All fields from view '{vname}' have been removed.",
        ))

    old_explores = {e.get("explore_name", "") for e in snapshot_entries if e.get("explore_name")}
    new_explores = {t.explore_name for t in current_terms if t.explore_name}
    for ename in sorted(old_explores - new_explores):
        items.append(DriftItem(
            category="explore_removed",
            severity="error",
            field_id=ename,
            message=f"All fields from explore '{ename}' have been removed.",
        ))

    # Sort by severity rank, then category, then field_id
    items.sort(key=lambda d: (SEVERITY_RANK.get(d.severity, 99), d.category, d.field_id))
    return items


def filter_by_severity(items: list[DriftItem], min_severity: str) -> list[DriftItem]:
    """Filter drift items to only include those at or above min_severity."""
    threshold = SEVERITY_RANK.get(min_severity, 2)
    return [d for d in items if SEVERITY_RANK.get(d.severity, 99) <= threshold]


def format_text(items: list[DriftItem]) -> str:
    """Format drift items as human-readable text."""
    if not items:
        return "No drift detected."
    lines = []
    for d in items:
        prefix = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}.get(d.severity, "?????")
        line = f"[{prefix}] {d.category}: {d.message}"
        if d.old_value is not None:
            line += f"\n         old: {d.old_value}"
        if d.new_value is not None:
            line += f"\n         new: {d.new_value}"
        lines.append(line)
    errors = sum(1 for d in items if d.severity == "error")
    warnings = sum(1 for d in items if d.severity == "warning")
    infos = sum(1 for d in items if d.severity == "info")
    lines.append(f"\nSummary: {errors} error(s), {warnings} warning(s), {infos} info(s)")
    return "\n".join(lines)


def format_json(items: list[DriftItem]) -> str:
    """Format drift items as JSON."""
    data = {
        "drift": [
            {
                "category": d.category,
                "severity": d.severity,
                "field_id": d.field_id,
                "message": d.message,
                "old_value": d.old_value,
                "new_value": d.new_value,
            }
            for d in items
        ],
        "summary": {
            "total": len(items),
            "errors": sum(1 for d in items if d.severity == "error"),
            "warnings": sum(1 for d in items if d.severity == "warning"),
            "info": sum(1 for d in items if d.severity == "info"),
        },
    }
    return json.dumps(data, indent=2)

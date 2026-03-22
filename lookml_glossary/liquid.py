"""Static extraction of all SQL branches from LookML Liquid templates.

LookML uses Liquid templates ({% if %}, {% case %}, etc.) to produce
different SQL depending on runtime context (user attributes, filters,
selected fields).  Since we have no runtime context, this module parses
the Liquid AST and enumerates **every possible text output** so the
caller can analyse all branches statically.

Only the structure is inspected — no Liquid expressions are evaluated.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Attempt to import python-liquid; degrade gracefully if not installed.
try:
    from liquid import Environment as _LiquidEnv
    from liquid.builtin.tags.if_tag import IfNode
    from liquid.builtin.tags.case_tag import CaseNode
    from liquid.builtin.tags.for_tag import ForNode
    from liquid.builtin.content import ContentNode
    from liquid.ast import BlockNode, ConditionalBlockNode, Node

    _HAS_LIQUID = True
except ImportError:  # pragma: no cover
    _HAS_LIQUID = False

# Quick regex check — avoid parsing overhead for non-Liquid strings.
_LIQUID_TAG_RE = re.compile(r'\{%.*?%\}', re.DOTALL)

# Cap the number of branch combinations to prevent combinatorial explosion
# in deeply nested templates. 64 covers 6 levels of binary if/else nesting.
_MAX_BRANCHES = 64


def has_liquid(sql: str) -> bool:
    """Return True if the string contains Liquid template tags."""
    return bool(_LIQUID_TAG_RE.search(sql))


def extract_liquid_branches(sql: str) -> list[str]:
    """Return all possible SQL strings produced by a Liquid template.

    If the input contains no Liquid tags, returns ``[sql]`` unchanged.
    If python-liquid is not installed, falls back to regex extraction.

    Each returned string is one possible rendering of the template
    (one combination of if/case branches).
    """
    if not has_liquid(sql):
        return [sql]

    if not _HAS_LIQUID:
        return _extract_branches_regex(sql)

    try:
        env = _LiquidEnv()
        template = env.from_string(sql)
    except Exception as exc:
        logger.debug("Liquid parse failed (%s); falling back to regex.", exc)
        return _extract_branches_regex(sql)

    branches = _walk_nodes(template.nodes)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for b in branches:
        cleaned = b.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique or [sql]


# ---------------------------------------------------------------------------
# AST walker
# ---------------------------------------------------------------------------

def _walk_nodes(nodes: list) -> list[str]:
    """Walk a list of AST nodes and return all possible text outputs.

    Each node is either static text (ContentNode/OutputNode) or a
    branching construct (IfNode/CaseNode).  Static text produces one
    output; branching constructs multiply the number of outputs by
    the number of branches.
    """
    # Start with a single empty branch
    branches = [""]

    for node in nodes:
        node_type = type(node).__name__

        if node_type == "ContentNode":
            # Static text — append to every existing branch
            text = str(node)
            branches = [b + text for b in branches]

        elif node_type == "OutputNode":
            # {{ variable }} — we can't evaluate it, insert a placeholder
            expr = str(node.expression) if hasattr(node, "expression") else "?"
            placeholder = f"{{{{ {expr} }}}}"
            branches = [b + placeholder for b in branches]

        elif node_type == "IfNode":
            branches = _expand_if(node, branches)

        elif node_type == "CaseNode":
            branches = _expand_case(node, branches)

        elif node_type == "ForNode":
            # For loops: extract the body as one branch (we can't iterate)
            if hasattr(node, "block"):
                body_branches = _walk_block(node.block)
                branches = _combine(branches, body_branches)

        else:
            # Unknown node — render to string as best-effort
            text = str(node) if node else ""
            if text:
                branches = [b + text for b in branches]

        # Guard against combinatorial explosion
        if len(branches) > _MAX_BRANCHES:
            branches = branches[:_MAX_BRANCHES]
            logger.debug("Liquid branch limit (%d) reached; truncating.", _MAX_BRANCHES)
            break

    return branches


def _walk_block(block) -> list[str]:
    """Walk a BlockNode (which wraps a list of child nodes)."""
    if block is None:
        return [""]
    # BlockNode has .nodes; ConditionalBlockNode doesn't but str() works
    if hasattr(block, "nodes"):
        return _walk_nodes(block.nodes)
    # Fallback: render to string
    text = str(block)
    return [text] if text else [""]


def _expand_if(node, current_branches: list[str]) -> list[str]:
    """Expand an IfNode into all its branches (if / elsif / else)."""
    branch_sets: list[list[str]] = []

    # Primary consequence (the "if" branch)
    if hasattr(node, "consequence") and node.consequence is not None:
        branch_sets.append(_walk_block(node.consequence))

    # Alternatives (elsif branches)
    if hasattr(node, "alternatives"):
        for alt in (node.alternatives or []):
            if hasattr(alt, "block") and alt.block is not None:
                branch_sets.append(_walk_block(alt.block))
            else:
                branch_sets.append([str(alt)])

    # Default (else branch)
    if hasattr(node, "default") and node.default is not None:
        branch_sets.append(_walk_block(node.default))

    if not branch_sets:
        return current_branches

    # Flatten all branch alternatives
    all_alternatives = [text for branch in branch_sets for text in branch]
    return _combine(current_branches, all_alternatives)


def _expand_case(node, current_branches: list[str]) -> list[str]:
    """Expand a CaseNode into all its when/else branches."""
    branch_sets: list[list[str]] = []

    if hasattr(node, "blocks"):
        for block in (node.blocks or []):
            if hasattr(block, "block"):
                branch_sets.append(_walk_block(block.block))
            elif hasattr(block, "nodes"):
                branch_sets.append(_walk_nodes(block.nodes))
            else:
                branch_sets.append([str(block)])

    if not branch_sets:
        return current_branches

    all_alternatives = [text for branch in branch_sets for text in branch]
    return _combine(current_branches, all_alternatives)


def _combine(prefixes: list[str], suffixes: list[str]) -> list[str]:
    """Combine every prefix with every suffix (Cartesian product)."""
    if not suffixes:
        return prefixes
    result = []
    for p in prefixes:
        for s in suffixes:
            result.append(p + s)
            if len(result) > _MAX_BRANCHES:
                return result[:_MAX_BRANCHES]
    return result


# ---------------------------------------------------------------------------
# Regex fallback (when python-liquid is not installed)
# ---------------------------------------------------------------------------

def _extract_branches_regex(sql: str) -> list[str]:
    """Best-effort branch extraction using regex when python-liquid is unavailable.

    Finds {% if %}...{% elsif %}...{% else %}...{% endif %} blocks and
    extracts each branch body. Also handles {% case %}...{% when %}...{% endcase %}.
    Does not handle nesting — returns each branch body individually.
    """
    branches: list[str] = []

    # Strip all Liquid tags to get the "base" SQL
    stripped = re.sub(r'\{%.*?%\}', '', sql, flags=re.DOTALL).strip()
    if stripped:
        branches.append(stripped)

    # Extract if/elsif/else bodies
    # Split on Liquid tags and collect text between them
    parts = re.split(r'\{%.*?%\}', sql, flags=re.DOTALL)
    for part in parts:
        cleaned = part.strip()
        if cleaned and cleaned not in branches:
            branches.append(cleaned)

    return branches or [sql]

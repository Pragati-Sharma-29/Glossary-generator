"""Command-line interface for the LookML Glossary Generator."""

import argparse
import os
import sys

from .generator import generate_csv, generate_html, generate_json, generate_markdown, generate_webapp
from .parser import parse_lookml_model


def _add_common_args(sub: argparse.ArgumentParser) -> None:
    """Add arguments shared between generate and validate subcommands."""
    sub.add_argument(
        "model",
        help="Path to the LookML model file (.model.lkml or .lkml)",
    )
    sub.add_argument(
        "-I", "--include-path",
        action="append",
        default=[],
        help="Additional directories to search for included LookML files (can be repeated)",
    )


def _validate_output_path(path: str) -> str:
    """Resolve and validate an output path stays within cwd."""
    resolved = os.path.realpath(path)
    cwd = os.path.realpath(os.getcwd())
    if not (resolved.startswith(cwd + os.sep) or resolved == cwd):
        print("Error: output path must be within the current working directory.", file=sys.stderr)
        sys.exit(1)
    return resolved


def _cmd_generate(args: argparse.Namespace) -> None:
    """Run the generate subcommand."""
    terms = parse_lookml_model(args.model, include_paths=args.include_path)

    if not terms:
        print("No glossary terms found.", file=sys.stderr)
        sys.exit(1)

    generators = {
        "json": generate_json,
        "markdown": generate_markdown,
        "html": generate_html,
        "csv": generate_csv,
        "webapp": generate_webapp,
    }
    gen_fn = generators[args.format]

    if args.output:
        resolved_output = _validate_output_path(args.output)
        output_dir = os.path.dirname(resolved_output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(resolved_output, "w") as f:
            gen_fn(terms, f)
        print(f"Glossary written to {args.output} ({len(terms)} terms)", file=sys.stderr)
    else:
        gen_fn(terms, sys.stdout)


def _cmd_validate(args: argparse.Namespace) -> None:
    """Run the validate subcommand."""
    from .validator import (
        filter_by_severity,
        format_json,
        format_text,
        load_snapshot,
        validate,
        SEVERITY_RANK,
    )

    snapshot_entries = load_snapshot(args.snapshot)
    current_terms = parse_lookml_model(args.model, include_paths=args.include_path)

    if not current_terms:
        print("No glossary terms found in current model.", file=sys.stderr)
        sys.exit(1)

    all_items = validate(snapshot_entries, current_terms)
    visible = filter_by_severity(all_items, args.severity)

    if args.format == "json":
        output = format_json(visible)
    else:
        output = format_text(visible)

    if args.output:
        resolved_output = _validate_output_path(args.output)
        output_dir = os.path.dirname(resolved_output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(resolved_output, "w") as f:
            f.write(output)
        print(f"Validation report written to {args.output}", file=sys.stderr)
    else:
        print(output)

    # Exit non-zero if any drift at or above --fail-on level
    fail_threshold = SEVERITY_RANK.get(args.fail_on, 0)
    failing = [d for d in all_items if SEVERITY_RANK.get(d.severity, 99) <= fail_threshold]
    if failing:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LookML Glossary Generator — generate and validate glossaries.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- generate subcommand --------------------------------------------------
    gen_parser = subparsers.add_parser(
        "generate",
        help="Generate a glossary from a LookML model file.",
    )
    _add_common_args(gen_parser)
    gen_parser.add_argument(
        "-f", "--format",
        choices=["json", "markdown", "html", "csv", "webapp"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    gen_parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output file path (default: stdout)",
    )

    # --- validate subcommand --------------------------------------------------
    val_parser = subparsers.add_parser(
        "validate",
        help="Detect drift between a glossary snapshot and current LookML source.",
    )
    _add_common_args(val_parser)
    val_parser.add_argument(
        "-s", "--snapshot",
        required=True,
        help="Path to a previously generated JSON glossary snapshot.",
    )
    val_parser.add_argument(
        "-f", "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the drift report (default: text)",
    )
    val_parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output file path (default: stdout)",
    )
    val_parser.add_argument(
        "--severity",
        choices=["info", "warning", "error"],
        default="warning",
        help="Minimum severity to include in output (default: warning)",
    )
    val_parser.add_argument(
        "--fail-on",
        choices=["info", "warning", "error"],
        default="error",
        help="Exit non-zero if any drift at this severity or above (default: error)",
    )

    # --- backward compat: bare positional → generate --------------------------
    # Detect old-style invocation (no subcommand, bare .lkml path) before
    # argparse can reject it as an invalid subcommand choice.
    if len(sys.argv) > 1 and sys.argv[1].endswith(".lkml"):
        sys.argv.insert(1, "generate")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "generate":
        _cmd_generate(args)
    elif args.command == "validate":
        _cmd_validate(args)


if __name__ == "__main__":
    main()

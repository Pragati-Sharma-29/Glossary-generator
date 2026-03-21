"""Command-line interface for the LookML Glossary Generator."""

import argparse
import sys

from .generator import generate_html, generate_json, generate_markdown
from .parser import parse_lookml_model


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a glossary from a LookML model file.",
    )
    parser.add_argument(
        "model",
        help="Path to the LookML model file (.model.lkml or .lkml)",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "markdown", "html"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "-I", "--include-path",
        action="append",
        default=[],
        help="Additional directories to search for included LookML files (can be repeated)",
    )

    args = parser.parse_args()

    terms = parse_lookml_model(args.model, include_paths=args.include_path)

    if not terms:
        print("No glossary terms found.", file=sys.stderr)
        sys.exit(1)

    generators = {
        "json": generate_json,
        "markdown": generate_markdown,
        "html": generate_html,
    }
    gen_fn = generators[args.format]

    if args.output:
        with open(args.output, "w") as f:
            gen_fn(terms, f)
        print(f"Glossary written to {args.output} ({len(terms)} terms)", file=sys.stderr)
    else:
        gen_fn(terms, sys.stdout)


if __name__ == "__main__":
    main()

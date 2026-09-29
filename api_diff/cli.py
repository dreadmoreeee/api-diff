"""Command line interface."""
from __future__ import annotations

import argparse
import sys

from .diff import BREAKING, diff_specs
from .loader import ApiDiffError, load_spec
from .report import to_json, to_markdown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="api-diff",
                                 description="Compare two OpenAPI 3.x specs and classify each change.")
    ap.add_argument("old", help="old spec (YAML or JSON)")
    ap.add_argument("new", help="new spec (YAML or JSON)")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("-o", "--output", metavar="FILE", help="write the report to FILE")
    ap.add_argument("--fail-on-breaking", action="store_true",
                    help="exit 1 when breaking changes are found")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage
        return int(exc.code or 0)
    try:
        changes = diff_specs(load_spec(args.old), load_spec(args.new))
        out = to_json(changes) if args.format == "json" else to_markdown(changes)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out + "\n")
        else:
            sys.stdout.buffer.write((out + "\n").encode("utf-8"))
            sys.stdout.flush()
    except (ApiDiffError, OSError) as exc:
        print(f"api-diff: error: {exc}", file=sys.stderr)
        return 2
    if args.fail_on_breaking and any(c.severity == BREAKING for c in changes):
        return 1
    return 0

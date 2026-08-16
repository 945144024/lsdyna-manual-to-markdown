"""Command-line entry point for lsdyna-manual-builder."""

from __future__ import annotations

import argparse
import sys

from lsdyna_manual import __version__
from lsdyna_manual.config import ConfigError
from lsdyna_manual.pipeline import EXIT_FAILED, EXIT_SUCCESS, run_build, run_inspection


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lsdyna-manual",
        description=(
            "Build a Markdown corpus from a user-supplied LS-DYNA Keyword Manual."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser(
        "build", help="run the build pipeline described by a config file"
    )
    build.add_argument("config", help="path to the YAML config file")
    inspect_cmd = subparsers.add_parser(
        "inspect",
        help="run deterministic document inspection (PageMap/SectionMap)",
    )
    inspect_cmd.add_argument("config", help="path to the YAML config file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.command == "build":
            result = run_build(args.config)
            return result.exit_code
        if args.command == "inspect":
            run_inspection(args.config)
            return EXIT_SUCCESS
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

"""Command-line entry point for lsdyna-manual-builder."""

from __future__ import annotations

import argparse
import sys

from lsdyna_manual import __version__
from lsdyna_manual.config import ConfigError
from lsdyna_manual.pipeline import EXIT_FAILED, run_build


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "build":
        try:
            result = run_build(args.config)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FAILED
        return result.exit_code
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

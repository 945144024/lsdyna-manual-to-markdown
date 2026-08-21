"""Command-line entry point for LS-DYNA Manual to Markdown."""

from __future__ import annotations

import argparse
import sys

from lsdyna_manual import __version__
from lsdyna_manual.config import ConfigError
from lsdyna_manual.pipeline import (
    EXIT_FAILED,
    EXIT_SUCCESS,
    run_build,
    run_inspection,
    run_parsing,
    run_reconstruction,
)
from lsdyna_manual.preflight import run_preflight
from lsdyna_manual.providers.base import ProviderError
from lsdyna_manual.regression_sampling import (
    run_manifest_detection,
    run_sampling,
    sample_page_reference_count,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manual-to-markdown",
        description=(
            "Build a Markdown corpus from user-supplied LS-DYNA Manuals."
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
    build.add_argument(
        "--allow-runtime-install",
        action="store_true",
        help=(
            "allow the local provider to install PaddleOCR dependencies and "
            "download configured runtime artifacts"
        ),
    )
    inspect_cmd = subparsers.add_parser(
        "inspect",
        help="run deterministic document inspection (PageMap/SectionMap)",
    )
    inspect_cmd.add_argument("config", help="path to the YAML config file")
    parse_cmd = subparsers.add_parser(
        "parse",
        help="parse inspected pages with resumable PaddleOCR batches",
    )
    parse_cmd.add_argument("config", help="path to the YAML config file")
    parse_cmd.add_argument(
        "--document",
        help="limit parsing to one document id, such as keyword-volume-2",
    )
    parse_cmd.add_argument(
        "--max-pages",
        type=int,
        help="parse at most this many unique pages from the selected plan",
    )
    parse_cmd.add_argument(
        "--allow-runtime-install",
        action="store_true",
        help=(
            "allow the local provider to install PaddleOCR dependencies and "
            "download configured runtime artifacts"
        ),
    )
    parse_cmd.add_argument(
        "--sample-manifest",
        help="parse only the pages listed in a semantic sample manifest",
    )
    parse_cmd.add_argument(
        "--intermediate-dir",
        help="override the directory containing PageMap / SectionMap artifacts",
    )
    reconstruct_cmd = subparsers.add_parser(
        "reconstruct",
        help="render Markdown corpus from existing PageIR artifacts",
    )
    reconstruct_cmd.add_argument("config", help="path to the YAML config file")
    reconstruct_cmd.add_argument(
        "--document",
        help="limit reconstruction to one document id, such as keyword-volume-2",
    )
    sample_cmd = subparsers.add_parser(
        "sample-regression",
        help="select and detect a reproducible semantic regression sample",
    )
    sample_cmd.add_argument("--manuals-dir", default="manuals")
    sample_cmd.add_argument("--release", default="R17")
    sample_cmd.add_argument(
        "--intermediate-dir",
        default="workspace/regression/r17/intermediate",
    )
    sample_cmd.add_argument(
        "--pageir-dir",
        default="workspace/run_r17/parsing/pageir",
    )
    sample_cmd.add_argument(
        "--output-dir",
        default="workspace/regression/r17/semantic-sample",
    )
    sample_cmd.add_argument("--seed", type=int, default=20260817)
    sample_cmd.add_argument(
        "--sample-manifest",
        help="detect this frozen manifest instead of selecting a new sample",
    )
    sample_cmd.add_argument(
        "--holdout-of",
        help="select an independent set excluding sections in this frozen manifest",
    )
    subparsers.add_parser(
        "doctor", help="check configuration and runtime prerequisites without inference"
    ).add_argument("config", help="path to the YAML config file")
    sample_cmd.add_argument(
        "--anchor",
        action="append",
        default=[],
        metavar="DOCUMENT_ID:SECTION_ID",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.command == "build":
            preflight = run_preflight(args.config)
            if preflight.failed:
                return EXIT_FAILED
            result = run_build(
                args.config,
                allow_runtime_install=args.allow_runtime_install,
            )
            return result.exit_code
        if args.command == "doctor":
            result = run_preflight(args.config)
            return EXIT_FAILED if result.failed else EXIT_SUCCESS
        if args.command == "inspect":
            run_inspection(args.config)
            return EXIT_SUCCESS
        if args.command == "parse":
            result = run_parsing(
                args.config,
                document_id=args.document,
                max_pages=args.max_pages,
                allow_runtime_install=args.allow_runtime_install,
                sample_manifest_path=args.sample_manifest,
                intermediate_dir=args.intermediate_dir,
            )
            return result.exit_code
        if args.command == "reconstruct":
            result = run_reconstruction(
                args.config,
                document_id=args.document,
            )
            return result.exit_code
        if args.command == "sample-regression":
            anchors = []
            for value in args.anchor:
                if ":" not in value:
                    raise ConfigError(
                        f"invalid --anchor {value!r}; expected DOCUMENT_ID:SECTION_ID"
                    )
                anchors.append(tuple(value.split(":", 1)))
            try:
                if args.sample_manifest is not None:
                    if anchors or args.holdout_of:
                        raise ConfigError(
                            "--anchor and --holdout-of cannot be combined with "
                            "--sample-manifest"
                        )
                    manifest, report = run_manifest_detection(
                        manifest_path=args.sample_manifest,
                        manuals_dir=args.manuals_dir,
                        release=args.release,
                        intermediate_dir=args.intermediate_dir,
                        pageir_root=args.pageir_dir,
                        output_dir=args.output_dir,
                    )
                else:
                    manifest, report = run_sampling(
                        manuals_dir=args.manuals_dir,
                        release=args.release,
                        intermediate_dir=args.intermediate_dir,
                        pageir_root=args.pageir_dir,
                        output_dir=args.output_dir,
                        seed=args.seed,
                        anchor_sections=anchors,
                        exclude_manifest_path=args.holdout_of,
                    )
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            print(
                f"samples={manifest['summary']['sample_count']} "
                f"pages={sample_page_reference_count(manifest)} "
                f"checked={report['summary']['checked_count']} "
                f"partial={report['summary']['partial_count']} "
                f"not_parsed={report['summary']['not_parsed_count']}"
            )
            return EXIT_SUCCESS
    except (ConfigError, ProviderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

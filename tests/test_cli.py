"""Tests for the public command-line identity."""

from lsdyna_manual.cli.main import build_arg_parser


def test_public_cli_uses_manual_to_markdown_name():
    parser = build_arg_parser()

    assert parser.prog == "manual-to-markdown"
    assert parser.format_usage().startswith("usage: manual-to-markdown ")

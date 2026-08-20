"""Platform-safe tests for Poppler text extraction."""

from types import SimpleNamespace

import lsdyna_manual.parser.text_extractor as text_extractor
from lsdyna_manual.parser.text_extractor import PopplerLayoutExtractor


def test_poppler_output_is_decoded_as_utf8_on_every_platform(monkeypatch, tmp_path):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF synthetic")
    calls = []

    monkeypatch.setattr(text_extractor.shutil, "which", lambda _name: "pdftotext")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="alpha\n中文\fomega\f".encode("utf-8"))

    monkeypatch.setattr(text_extractor.subprocess, "run", run)

    assert PopplerLayoutExtractor().extract_pages(pdf) == ["alpha\n中文", "omega"]
    assert calls == [
        (["pdftotext", "-layout", str(pdf), "-"], {"capture_output": True, "check": True})
    ]


def test_poppler_invalid_utf8_is_preserved_as_replacement_character(
    monkeypatch, tmp_path
):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF synthetic")
    monkeypatch.setattr(text_extractor.shutil, "which", lambda _name: "pdftotext")
    monkeypatch.setattr(
        text_extractor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=b"before\x95after\f"),
    )

    assert PopplerLayoutExtractor().extract_pages(pdf) == ["before\ufffdafter"]

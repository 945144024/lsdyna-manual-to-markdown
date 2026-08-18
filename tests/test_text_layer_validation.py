"""Tests for PDF text-layer and visual PageIR sampling."""

from pathlib import Path

from lsdyna_manual.parser.page_ir import MathBlock, PageIR, TableBlock, TextBlock
from lsdyna_manual.validation.text_layer import compare_text_layer_samples


class FakeExtractor:
    def __init__(self, pages):
        self.pages = pages

    def extract_pages(self, pdf_path: Path):
        return self.pages


def test_text_layer_sample_matches_pageir_tokens(tmp_path):
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[
            TextBlock(text="EOSID A B"),
            TableBlock(rows=[]),
        ],
    )
    report = compare_text_layer_samples(
        document_id="keyword-volume-2",
        pdf_path=tmp_path / "manual.pdf",
        page_irs={1: page},
        extractor=FakeExtractor(["EOSID A B"]),
        sample_count=1,
        min_tokens=1,
        min_visual_recall=1.0,
    )

    assert len(report.samples) == 1
    assert report.samples[0].visual_recall == 1.0
    assert report.samples[0].text_layer_recall == 1.0
    assert report.issues == []


def test_text_layer_sample_reports_divergence_and_missing_tokens(tmp_path):
    page = PageIR(
        document_id="keyword-volume-2",
        pdf_page=1,
        manual_page="2-1",
        blocks=[TextBlock(text="alpha beta gamma delta epsilon zeta")],
    )
    report = compare_text_layer_samples(
        document_id="keyword-volume-2",
        pdf_path=tmp_path / "manual.pdf",
        page_irs={1: page},
        extractor=FakeExtractor(["alpha"]),
        sample_count=1,
        min_tokens=1,
        min_visual_recall=0.65,
    )

    assert report.samples[0].visual_recall < 0.65
    assert any(issue.code == "TEXT_LAYER_DIVERGENCE" for issue in report.issues)
    assert "beta" in report.samples[0].missing_visual_tokens


def test_text_layer_distinguishes_formula_representation_divergence(tmp_path):
    page = PageIR(
        document_id="theory",
        pdf_page=1,
        manual_page="1-1",
        blocks=[
            TextBlock(text="Conservation equation"),
            MathBlock(text=r"\frac{\partial \mathbf{x}}{\partial t}"),
        ],
    )
    report = compare_text_layer_samples(
        document_id="theory",
        pdf_path=tmp_path / "manual.pdf",
        page_irs={1: page},
        extractor=FakeExtractor(["Conservation equation ∂x/∂t"]),
        sample_count=1,
        min_tokens=1,
        min_visual_recall=0.65,
    )

    sample = report.samples[0]
    assert sample.visual_recall < 0.65
    assert sample.prose_visual_recall == 1.0
    assert {
        issue.code for issue in report.issues
    } == {"TEXT_LAYER_FORMULA_REPRESENTATION_DIVERGENCE"}


def test_text_layer_sampling_uses_deterministic_first_middle_last_pages(tmp_path):
    pages = {
        page: PageIR(
            document_id="keyword-volume-2",
            pdf_page=page,
            manual_page=f"2-{page}",
            blocks=[TextBlock(text=f"page {page}")],
        )
        for page in range(1, 7)
    }
    report = compare_text_layer_samples(
        document_id="keyword-volume-2",
        pdf_path=tmp_path / "manual.pdf",
        page_irs=pages,
        extractor=FakeExtractor([f"page {page}" for page in range(1, 7)]),
        sample_count=3,
        min_tokens=1,
    )

    assert [sample.pdf_page for sample in report.samples] == [1, 4, 6]

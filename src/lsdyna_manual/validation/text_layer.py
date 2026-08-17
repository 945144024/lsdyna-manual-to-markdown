"""Deterministic, sample-based comparison of PDF text and visual PageIR."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from lsdyna_manual.parser.page_ir import (
    Block,
    FigureBlock,
    PageIR,
    ParseIssue,
    TableBlock,
    TextBlock,
)
from lsdyna_manual.parser.text_extractor import TextExtractor


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")


@dataclass(frozen=True)
class TextLayerSample:
    document_id: str
    pdf_page: int
    manual_page: str | None
    visual_token_count: int
    text_layer_token_count: int
    overlap_token_count: int
    visual_recall: float | None
    text_layer_recall: float | None
    missing_visual_tokens: tuple[str, ...] = ()
    issues: tuple[ParseIssue, ...] = ()

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "pdf_page": self.pdf_page,
            "manual_page": self.manual_page,
            "visual_token_count": self.visual_token_count,
            "text_layer_token_count": self.text_layer_token_count,
            "overlap_token_count": self.overlap_token_count,
            "visual_recall": self.visual_recall,
            "text_layer_recall": self.text_layer_recall,
            "missing_visual_tokens": list(self.missing_visual_tokens),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class TextLayerComparisonReport:
    document_id: str
    samples: list[TextLayerSample] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "samples": [sample.to_dict() for sample in self.samples],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _tokens(text: str) -> Counter[str]:
    normalized = text.replace("\\n", " ").casefold()
    return Counter(match.group(0) for match in _TOKEN_RE.finditer(normalized))


def _block_text(block: Block) -> str:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, TableBlock):
        return "\n".join(
            " ".join(cell.text for cell in row)
            for row in block.rows
        )
    if isinstance(block, FigureBlock):
        return ""
    return getattr(block, "text", "")


def pageir_text(page_ir: PageIR) -> str:
    """Flatten visible PageIR text for comparison without changing PageIR."""

    return "\n".join(_block_text(block) for block in page_ir.blocks)


def _sample_pages(page_irs: Mapping[int, PageIR], sample_count: int) -> list[int]:
    pages = sorted(page_irs)
    if sample_count <= 0 or len(pages) <= sample_count:
        return pages
    if sample_count == 1:
        return [pages[0]]
    indices = {
        int(index * (len(pages) - 1) / (sample_count - 1) + 0.5)
        for index in range(sample_count)
    }
    return [pages[index] for index in sorted(indices)]


def compare_page_text(
    *,
    document_id: str,
    page_ir: PageIR,
    text_layer: str,
    min_tokens: int,
    min_visual_recall: float,
) -> TextLayerSample:
    visual = _tokens(pageir_text(page_ir))
    layer = _tokens(text_layer)
    overlap = sum((visual & layer).values())
    visual_total = sum(visual.values())
    layer_total = sum(layer.values())
    visual_recall = overlap / visual_total if visual_total else None
    layer_recall = overlap / layer_total if layer_total else None
    missing = tuple(
        token
        for token, _count in (visual - layer).most_common(20)
    )
    issues: list[ParseIssue] = []
    if visual_total >= min_tokens and (
        visual_recall is None or visual_recall < min_visual_recall
    ):
        issues.append(
            ParseIssue(
                severity="warning",
                code="TEXT_LAYER_DIVERGENCE",
                message=(
                    f"PageIR/text-layer token recall is "
                    f"{visual_recall:.3f} (threshold {min_visual_recall:.3f}); "
                    f"missing visual tokens: {', '.join(missing[:8])}"
                ),
            )
        )
    return TextLayerSample(
        document_id=document_id,
        pdf_page=page_ir.pdf_page,
        manual_page=page_ir.manual_page,
        visual_token_count=visual_total,
        text_layer_token_count=layer_total,
        overlap_token_count=overlap,
        visual_recall=visual_recall,
        text_layer_recall=layer_recall,
        missing_visual_tokens=missing,
        issues=tuple(issues),
    )


def compare_text_layer_samples(
    *,
    document_id: str,
    pdf_path: Path,
    page_irs: Mapping[int, PageIR],
    extractor: TextExtractor,
    sample_count: int = 3,
    min_tokens: int = 8,
    min_visual_recall: float = 0.65,
) -> TextLayerComparisonReport:
    """Compare deterministic samples and return report-ready issues."""

    report = TextLayerComparisonReport(document_id=document_id)
    pages = _sample_pages(page_irs, sample_count)
    if not pages:
        return report
    text_pages = extractor.extract_pages(pdf_path)
    for pdf_page in pages:
        page_ir = page_irs[pdf_page]
        if pdf_page <= 0 or pdf_page > len(text_pages):
            report.issues.append(
                ParseIssue(
                    severity="warning",
                    code="TEXT_LAYER_PAGE_UNAVAILABLE",
                    message=(
                        f"pdftotext returned {len(text_pages)} pages; "
                        f"PDF page {pdf_page} is unavailable"
                    ),
                )
            )
            continue
        sample = compare_page_text(
            document_id=document_id,
            page_ir=page_ir,
            text_layer=text_pages[pdf_page - 1],
            min_tokens=min_tokens,
            min_visual_recall=min_visual_recall,
        )
        report.samples.append(sample)
        report.issues.extend(sample.issues)
    return report

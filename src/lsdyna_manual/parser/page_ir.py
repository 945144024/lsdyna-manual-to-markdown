"""Canonical PageIR v0.1 data model.

This module deliberately implements only the schema described in
``docs/parser-interface.md``. The model is stable for the first
real-page validation round, but it is not final: fields may be added or
changed only when supported by observations from real Manual pages.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BBox = tuple[float, float, float, float] | None
SCHEMA_VERSION = "0.1"
BLOCK_TYPES = {
    "text",
    "table",
    "math",
    "figure",
    "header",
    "footer",
}
ALLOWED_SEVERITIES = {"info", "warning", "error"}


@dataclass
class ParseIssue:
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Block:
    """Base block carrying an optional layout bbox."""

    bbox: BBox = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.block_type()
        data["bbox"] = list(self.bbox) if self.bbox is not None else None
        return data

    @classmethod
    def block_type(cls) -> str:
        raise NotImplementedError


@dataclass
class TextBlock(Block):
    text: str = ""
    bbox: BBox = None

    @classmethod
    def block_type(cls) -> str:
        return "text"


@dataclass
class MathBlock(Block):
    text: str = ""
    bbox: BBox = None

    @classmethod
    def block_type(cls) -> str:
        return "math"


@dataclass
class FigureBlock(Block):
    text: str = ""
    bbox: BBox = None

    @classmethod
    def block_type(cls) -> str:
        return "figure"


@dataclass
class HeaderBlock(Block):
    text: str = ""
    bbox: BBox = None

    @classmethod
    def block_type(cls) -> str:
        return "header"


@dataclass
class FooterBlock(Block):
    text: str = ""
    bbox: BBox = None

    @classmethod
    def block_type(cls) -> str:
        return "footer"


@dataclass
class Cell:
    text: str
    row: int
    column: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TableBlock(Block):
    rows: list[list[Cell]] = field(default_factory=list)
    bbox: BBox = None

    @classmethod
    def block_type(cls) -> str:
        return "table"

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["rows"] = [[cell.to_dict() for cell in row] for row in self.rows]
        return data


@dataclass
class PageIR:
    pdf_page: int
    manual_page: str | None
    blocks: list[Block] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "pdf_page": self.pdf_page,
            "manual_page": self.manual_page,
            "blocks": [block.to_dict() for block in self.blocks],
            "issues": [issue.to_dict() for issue in self.issues],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageIR":
        return PageIR(
            pdf_page=int(data["pdf_page"]),
            manual_page=data.get("manual_page"),
            blocks=[block_from_dict(block) for block in data.get("blocks", [])],
            issues=[
                ParseIssue(
                    severity=issue["severity"],
                    code=issue["code"],
                    message=issue["message"],
                )
                for issue in data.get("issues", [])
            ],
        )


def block_from_dict(data: dict[str, Any]) -> Block:
    block_type = data["type"]
    bbox_data = data.get("bbox")
    bbox: BBox = (
        tuple(float(value) for value in bbox_data) if bbox_data is not None else None
    )

    if block_type == "text":
        return TextBlock(text=data.get("text", ""), bbox=bbox)
    if block_type == "math":
        return MathBlock(text=data.get("text", ""), bbox=bbox)
    if block_type == "figure":
        return FigureBlock(text=data.get("text", ""), bbox=bbox)
    if block_type == "header":
        return HeaderBlock(text=data.get("text", ""), bbox=bbox)
    if block_type == "footer":
        return FooterBlock(text=data.get("text", ""), bbox=bbox)
    if block_type == "table":
        rows = [
            [
                Cell(
                    text=cell["text"],
                    row=int(cell["row"]),
                    column=int(cell["column"]),
                )
                for cell in row
            ]
            for row in data.get("rows", [])
        ]
        return TableBlock(rows=rows, bbox=bbox)
    raise ValueError(f"unknown PageIR block type: {block_type}")


def _bbox_error(block: Block) -> list[ParseIssue]:
    issues: list[ParseIssue] = []
    if block.bbox is None:
        return issues
    if not isinstance(block.bbox, (list, tuple)) or len(block.bbox) != 4:
        issues.append(
            ParseIssue(
                severity="error",
                code="PAGEIR_INVALID_BBOX",
                message=f"bbox must be a 4-tuple or None, got {block.bbox!r}",
            )
        )
    return issues


def validate_page_ir(
    page_ir: PageIR, *, expected_pdf_page: int | None = None
) -> list[ParseIssue]:
    """Validate PageIR shape without adding new schema fields.

    This function checks identity and structural invariants only. It is
    intentionally small; the first real-page review decides whether the
    schema itself must change.
    """
    issues: list[ParseIssue] = []

    if page_ir.pdf_page <= 0:
        issues.append(
            ParseIssue(
                severity="error",
                code="PAGEIR_INVALID_PDF_PAGE",
                message=f"pdf_page must be positive, got {page_ir.pdf_page}",
            )
        )
    if expected_pdf_page is not None and page_ir.pdf_page != expected_pdf_page:
        issues.append(
            ParseIssue(
                severity="error",
                code="PAGEIR_PAGE_IDENTITY_MISMATCH",
                message=(
                    f"PageIR reports pdf_page {page_ir.pdf_page}, "
                    f"expected {expected_pdf_page}"
                ),
            )
        )

    for block in page_ir.blocks:
        issues.extend(_bbox_error(block))
        if isinstance(block, TableBlock):
            for row_index, row in enumerate(block.rows):
                for cell in row:
                    if cell.row != row_index:
                        issues.append(
                            ParseIssue(
                                severity="error",
                                code="PAGEIR_INVALID_TABLE_ROW",
                                message=(
                                    f"cell row {cell.row} does not match "
                                    f"table row {row_index}"
                                ),
                            )
                        )
                    if cell.column < 0:
                        issues.append(
                            ParseIssue(
                                severity="error",
                                code="PAGEIR_INVALID_TABLE_COLUMN",
                                message=f"negative table column: {cell.column}",
                            )
                        )

    for issue in page_ir.issues:
        if issue.severity not in ALLOWED_SEVERITIES:
            issues.append(
                ParseIssue(
                    severity="error",
                    code="PAGEIR_INVALID_ISSUE_SEVERITY",
                    message=(
                        f"severity must be one of {sorted(ALLOWED_SEVERITIES)}, "
                        f"got {issue.severity!r}"
                    ),
                )
            )

    return issues


def load_page_ir(path: Path) -> PageIR:
    import json

    return PageIR.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_page_ir(page_ir: PageIR, path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(page_ir.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

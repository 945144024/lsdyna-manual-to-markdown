"""Canonical PageIR v0.2 data model.

PageIR v0.2 preserves the v0.1 page and block identities while representing
HTML table row and column spans as logical cells. Existing v0.1 artifacts are
still readable; newly saved artifacts use v0.2.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BBox = tuple[float, float, float, float] | None
SCHEMA_VERSION = "0.2"
SUPPORTED_SCHEMA_VERSIONS = {"0.1", "0.2"}
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
    rowspan: int = 1
    colspan: int = 1

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


def table_row_widths(block: TableBlock) -> list[int]:
    """Return each row's occupied width, including logical cell spans."""

    widths = [0 for _ in block.rows]
    for row_index, row in enumerate(block.rows):
        for cell in row:
            if cell.row != row_index or cell.column < 0:
                continue
            colspan = max(1, cell.colspan)
            width = cell.column + colspan
            for covered_row in range(cell.row, min(cell.row + max(1, cell.rowspan), len(widths))):
                widths[covered_row] = max(widths[covered_row], width)
    return widths


def table_grid_rows(block: TableBlock) -> list[list[Cell]]:
    """Project logical cells to a rectangular grid without copying text.

    The anchor cell remains at its original coordinate. Covered positions are
    represented by empty synthetic cells so existing row-oriented semantic
    code can address the visual grid deterministically. The canonical PageIR
    still serializes only the logical source cells.
    """

    if not block.rows:
        return []
    if not any(
        cell.rowspan != 1 or cell.colspan != 1
        for row in block.rows
        for cell in row
    ):
        return [list(row) for row in block.rows]
    widths = table_row_widths(block)
    width = max(widths, default=0)
    grid = [
        [Cell(text="", row=row_index, column=column) for column in range(width)]
        for row_index in range(len(block.rows))
    ]
    for row_index, row in enumerate(block.rows):
        for cell in row:
            if cell.row != row_index or cell.column < 0 or cell.column >= width:
                continue
            grid[row_index][cell.column] = cell
    return grid


@dataclass
class PageIR:
    pdf_page: int
    manual_page: str | None
    blocks: list[Block] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    document_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "document_id": self.document_id,
            "pdf_page": self.pdf_page,
            "manual_page": self.manual_page,
            "blocks": [block.to_dict() for block in self.blocks],
            "issues": [issue.to_dict() for issue in self.issues],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageIR":
        schema_version = str(data.get("schema_version", "0.1"))
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported PageIR schema version: {schema_version}")
        return PageIR(
            document_id=data.get("document_id"),
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
                    rowspan=int(cell.get("rowspan", 1)),
                    colspan=int(cell.get("colspan", 1)),
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
    page_ir: PageIR,
    *,
    expected_document_id: str | None = None,
    expected_pdf_page: int | None = None,
) -> list[ParseIssue]:
    """Validate PageIR identity and table span invariants.

    This function checks identity and structural invariants only. It is
    intentionally small; the first real-page review decides whether the
    schema itself must change.
    """
    issues: list[ParseIssue] = []

    if expected_document_id is not None and page_ir.document_id != expected_document_id:
        issues.append(
            ParseIssue(
                severity="error",
                code="PAGEIR_DOCUMENT_IDENTITY_MISMATCH",
                message=(
                    f"PageIR reports document_id {page_ir.document_id!r}, "
                    f"expected {expected_document_id!r}"
                ),
            )
        )

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
                    if cell.rowspan <= 0 or cell.colspan <= 0:
                        issues.append(
                            ParseIssue(
                                severity="error",
                                code="PAGEIR_INVALID_TABLE_SPAN",
                                message=(
                                    "table cell rowspan and colspan must be positive: "
                                    f"{cell.rowspan}x{cell.colspan}"
                                ),
                            )
                        )

            occupied: dict[tuple[int, int], Cell] = {}
            for row_index, row in enumerate(block.rows):
                for cell in row:
                    if cell.row != row_index or cell.rowspan <= 0 or cell.colspan <= 0:
                        continue
                    for covered_row in range(cell.row, cell.row + cell.rowspan):
                        for covered_column in range(
                            cell.column, cell.column + cell.colspan
                        ):
                            key = (covered_row, covered_column)
                            previous = occupied.get(key)
                            if previous is not None and previous is not cell:
                                issues.append(
                                    ParseIssue(
                                        severity="error",
                                        code="PAGEIR_TABLE_SPAN_OVERLAP",
                                        message=(
                                            "table cell spans overlap at "
                                            f"row {covered_row}, column {covered_column}"
                                        ),
                                    )
                                )
                            occupied[key] = cell
                    if cell.row + cell.rowspan > len(block.rows):
                        issues.append(
                            ParseIssue(
                                severity="error",
                                code="PAGEIR_TABLE_SPAN_OUT_OF_BOUNDS",
                                message=(
                                    "table cell rowspan exceeds table height at "
                                    f"row {cell.row}"
                                ),
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

"""Keyword-level IR with block provenance and conservative boundary slicing."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from lsdyna_manual.parser.page_ir import (
    Block,
    Cell,
    FooterBlock,
    HeaderBlock,
    MathBlock,
    ParseIssue,
    TableBlock,
    TextBlock,
    table_grid_rows,
)
from lsdyna_manual.reconstruction.section_ir import SectionIR, SectionSourcePage


_LITERAL_CELL_NEWLINE_RE = re.compile(
    r"\\n(?!(?:abla|atural|e|eq|eg|ewcommand|ewline|i|obreak|olimits|ot|u|umber)\b)"
)


def normalize_literal_cell_newlines(text: str) -> str:
    """Convert OCR newline markers without damaging common LaTeX commands."""

    return _LITERAL_CELL_NEWLINE_RE.sub("\n", text)


def literal_cell_newline_count(text: str) -> int:
    return len(_LITERAL_CELL_NEWLINE_RE.findall(text))


@dataclass(frozen=True, order=True)
class BlockSourceRef:
    """Stable reference to a block without changing the PageIR schema."""

    document_id: str
    pdf_page: int
    block_index: int
    manual_page: str | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "document_id": self.document_id,
            "pdf_page": self.pdf_page,
            "manual_page": self.manual_page,
            "block_index": self.block_index,
        }


@dataclass(frozen=True)
class SourcedBlock:
    """A PageIR block coupled to its stable source reference."""

    source: BlockSourceRef
    block: Block

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "block": self.block.to_dict(),
        }


@dataclass
class BlockStream:
    """Ordered blocks owned by one Keyword candidate."""

    owned_sources: list[BlockSourceRef] = field(default_factory=list)
    content_blocks: list[SourcedBlock] = field(default_factory=list)
    ignored_blocks: list[SourcedBlock] = field(default_factory=list)


@dataclass
class OptionIR:
    name: str
    full_name: str | None = None
    blocks: list[SourcedBlock] = field(default_factory=list)


@dataclass(frozen=True)
class CardTableIR:
    """A semantic view over a row range of one source TableBlock."""

    source_block: SourcedBlock
    role: Literal["summary", "definition"]
    row_start: int
    row_end: int
    continuation_of: BlockSourceRef | None = None


@dataclass(frozen=True)
class TableCellSourceRef:
    """Provenance for a cell-derived Card field."""

    document_id: str
    pdf_page: int
    manual_page: str | None
    block_index: int
    row: int
    column: int

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "document_id": self.document_id,
            "pdf_page": self.pdf_page,
            "manual_page": self.manual_page,
            "block_index": self.block_index,
            "row": self.row,
            "column": self.column,
        }


@dataclass(frozen=True)
class CardFieldIR:
    slot: int
    variable: str | None
    field_type: str | None
    default: str | None
    source: TableCellSourceRef

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "variable": self.variable,
            "field_type": self.field_type,
            "default": self.default,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class CardConditionIR:
    """A condition expression copied from a Card source sentence."""

    variable: str
    operator: str
    values: tuple[str, ...]
    raw: str
    source_text: str
    source: BlockSourceRef

    def to_dict(self) -> dict:
        return {
            "variable": self.variable,
            "operator": self.operator,
            "values": list(self.values),
            "raw": self.raw,
            "source_text": self.source_text,
            "source": self.source.to_dict(),
        }


@dataclass
class CardIR:
    label: str
    condition_blocks: list[SourcedBlock] = field(default_factory=list)
    conditions: list[CardConditionIR] = field(default_factory=list)
    tables: list[CardTableIR] = field(default_factory=list)
    fields: list[CardFieldIR] = field(default_factory=list)
    continuation_of: str | None = None


@dataclass
class VariableDescriptionIR:
    variable: str
    applies_to: list[str] = field(default_factory=list)
    blocks: list[SourcedBlock] = field(default_factory=list)
    tables: list["VariableDescriptionTableIR"] = field(default_factory=list)


@dataclass(frozen=True)
class VariableDescriptionTableIR:
    """A variable description row range over one source TableBlock."""

    source_block: SourcedBlock
    row_start: int
    row_end: int
    continuation_of: BlockSourceRef | None = None


@dataclass
class KeywordIR:
    """Keyword semantics with complete accounting of owned PageIR blocks."""

    document_id: str
    section_id: str
    keyword_id: str
    name: str
    volume: int | None
    legacy_ids: list[str] = field(default_factory=list)
    source_pages: list[SectionSourcePage] = field(default_factory=list)
    owned_sources: list[BlockSourceRef] = field(default_factory=list)
    title_blocks: list[SourcedBlock] = field(default_factory=list)
    description_blocks: list[SourcedBlock] = field(default_factory=list)
    purpose_blocks: list[SourcedBlock] = field(default_factory=list)
    option_intro_blocks: list[SourcedBlock] = field(default_factory=list)
    options: list[OptionIR] = field(default_factory=list)
    card_intro_blocks: list[SourcedBlock] = field(default_factory=list)
    cards: list[CardIR] = field(default_factory=list)
    variable_description_blocks: list[SourcedBlock] = field(default_factory=list)
    variable_descriptions: list[VariableDescriptionIR] = field(default_factory=list)
    remarks_blocks: list[SourcedBlock] = field(default_factory=list)
    references_blocks: list[SourcedBlock] = field(default_factory=list)
    card_table_blocks: list[SourcedBlock] = field(default_factory=list)
    variable_catalog: list[str] = field(default_factory=list)
    _catalog_hints: list[str] = field(default_factory=list, repr=False)
    _catalog_hint_fields: list[CardFieldIR] = field(default_factory=list, repr=False)
    _catalog_alias_targets: dict[str, list[str]] = field(
        default_factory=dict, repr=False
    )
    unclassified_blocks: list[SourcedBlock] = field(default_factory=list)
    ignored_blocks: list[SourcedBlock] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    status: str = "failed"

    @property
    def manual_type(self) -> str:
        return "keyword"

    @property
    def option_names(self) -> list[str]:
        return [option.name for option in self.options]

    def content_blocks(self) -> list[SourcedBlock]:
        blocks = list(self.description_blocks)
        blocks.extend(self.purpose_blocks)
        blocks.extend(self.option_intro_blocks)
        for option in self.options:
            blocks.extend(option.blocks)
        blocks.extend(self.card_intro_blocks)
        for card in self.cards:
            blocks.extend(card.condition_blocks)
        blocks.extend(self.card_table_blocks)
        blocks.extend(self.variable_description_blocks)
        for description in self.variable_descriptions:
            blocks.extend(description.blocks)
        blocks.extend(self.remarks_blocks)
        blocks.extend(self.references_blocks)
        blocks.extend(self.unclassified_blocks)
        return sorted(
            blocks,
            key=lambda sourced: (
                sourced.source.pdf_page,
                sourced.source.block_index,
            ),
        )

    def accounted_blocks(self) -> list[SourcedBlock]:
        return [
            *self.title_blocks,
            *self.content_blocks(),
            *self.ignored_blocks,
        ]

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "section_id": self.section_id,
            "keyword_id": self.keyword_id,
            "name": self.name,
            "volume": self.volume,
            "legacy_ids": list(self.legacy_ids),
            "source_pages": [page.to_dict() for page in self.source_pages],
            "owned_sources": [source.to_dict() for source in self.owned_sources],
            "title_blocks": [block.to_dict() for block in self.title_blocks],
            "description_blocks": [
                block.to_dict() for block in self.description_blocks
            ],
            "purpose_blocks": [block.to_dict() for block in self.purpose_blocks],
            "option_intro_blocks": [
                block.to_dict() for block in self.option_intro_blocks
            ],
            "options": [
                {
                    "name": option.name,
                    "full_name": option.full_name,
                    "blocks": [block.to_dict() for block in option.blocks],
                }
                for option in self.options
            ],
            "card_intro_blocks": [
                block.to_dict() for block in self.card_intro_blocks
            ],
            "card_table_blocks": [
                block.to_dict() for block in self.card_table_blocks
            ],
            "cards": [
                {
                    "label": card.label,
                    "condition_blocks": [
                        block.to_dict() for block in card.condition_blocks
                    ],
                    "conditions": [
                        condition.to_dict() for condition in card.conditions
                    ],
                    "tables": [
                        {
                            "source_block": table.source_block.to_dict(),
                            "role": table.role,
                            "row_start": table.row_start,
                            "row_end": table.row_end,
                            "continuation_of": (
                                table.continuation_of.to_dict()
                                if table.continuation_of is not None
                                else None
                            ),
                        }
                        for table in card.tables
                    ],
                    "fields": [field.to_dict() for field in card.fields],
                    "continuation_of": card.continuation_of,
                }
                for card in self.cards
            ],
            "variable_catalog": list(self.variable_catalog),
            "variable_description_blocks": [
                block.to_dict() for block in self.variable_description_blocks
            ],
            "variable_descriptions": [
                {
                    "variable": description.variable,
                    "applies_to": list(description.applies_to),
                    "blocks": [block.to_dict() for block in description.blocks],
                    "tables": [
                        {
                            "source_block": table.source_block.to_dict(),
                            "row_start": table.row_start,
                            "row_end": table.row_end,
                            "continuation_of": (
                                table.continuation_of.to_dict()
                                if table.continuation_of is not None
                                else None
                            ),
                        }
                        for table in description.tables
                    ],
                }
                for description in self.variable_descriptions
            ],
            "remarks_blocks": [block.to_dict() for block in self.remarks_blocks],
            "references_blocks": [
                block.to_dict() for block in self.references_blocks
            ],
            "unclassified_blocks": [
                block.to_dict() for block in self.unclassified_blocks
            ],
            "ignored_blocks": [block.to_dict() for block in self.ignored_blocks],
            "issues": [issue.to_dict() for issue in self.issues],
            "status": self.status,
        }


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    source: BlockSourceRef | SectionSourcePage | None = None,
) -> ParseIssue:
    return ParseIssue(
        severity=severity,
        code=code,
        message=message,
        pdf_page=source.pdf_page if source is not None else None,
        manual_page=source.manual_page if source is not None else None,
    )


def _keyword_title_line_surface(text: str) -> str | None:
    """Strip only known display wrappers from a one-line Keyword title.

    Delimiters that may be part of a real keyword name are intentionally
    retained. The later canonicalization only treats spaces, underscores,
    and documented hyphen variants as interchangeable separators.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    line = re.sub(r"^#{1,6}\s*", "", lines[0]).strip()
    line = line.strip("`").strip()
    if line.startswith("**") and line.endswith("**") and len(line) > 4:
        line = line[2:-2].strip()

    line = unicodedata.normalize("NFKD", line)
    line = line.replace("‑", "-").replace("–", "-").replace("—", "-")
    line = line.replace("\\_", "_").replace("\\{", "{").replace("\\}", "}")

    # Paddle commonly renders a literal keyword asterisk as a small LaTeX
    # fragment. Remove that exact fragment, but leave other math punctuation
    # untouched so malformed or unrelated text cannot become an anchor.
    had_math_star = bool(
        re.search(r"\$\s*\^\s*\{\s*\*\s*\}\s*\$", line)
    )
    line = re.sub(r"\$\s*\^\s*\{\s*\*\s*\}\s*\$", "*", line)
    line = re.sub(r"\$\s*\{\s*\*\s*\}\s*\$", "*", line)
    line = re.sub(r"\^\s*\{\s*\*\s*\}", "*", line)

    # Keep the contents of a small, explicit set of text/math wrappers.
    line = re.sub(
        r"\\(?:mathrm|text|textrm|rm|bf|mathbf|mathit|it)\s*\{([^{}]*)\}",
        r"\1",
        line,
        flags=re.IGNORECASE,
    )
    stripped = line.strip()
    if (
        stripped.count("$") == 2
        and stripped.startswith("$")
        and stripped.endswith("$")
    ):
        line = stripped[1:-1].strip()
    elif had_math_star:
        # Some OCR variants leave the closing math delimiter after the
        # identifier even though the opening delimiter was consumed with the
        # asterisk fragment.
        line = re.sub(r"\s*\$\s*$", "", line)

    return re.sub(r"\s+", " ", line).strip() or None


def _normalized_title_line(text: str) -> str | None:
    """Return a conservative canonical key for a one-line Keyword title."""

    line = _keyword_title_line_surface(text)
    if line is None:
        return None
    line = re.sub(r"^\s*\*+\s*", "", line)
    # Spaces, underscores, and ASCII/typographic hyphens are the only
    # presentation separators treated as equivalent. Other punctuation is
    # retained and therefore cannot silently collapse distinct identifiers.
    line = re.sub(r"[ _-]+", "_", line)
    return line.upper() or None


def _keyword_title_match_rank(text: str, expected_name: str) -> int | None:
    """Return 2 for an exact title, 1 for an option-placeholder title."""

    candidate = _normalized_title_line(text)
    expected = _normalized_title_line(expected_name)
    if candidate is None or expected is None:
        return None
    if candidate == expected:
        return 2
    if not candidate.startswith(f"{expected}_"):
        return None
    suffix = candidate[len(expected) + 1 :]
    tokens = [token for token in suffix.split("_") if token]
    option_token = r"(?:OPTIONS?(?:[0-9]+)?|\{OPTIONS?(?:[0-9]+)?\})"
    if tokens and all(re.fullmatch(option_token, token) for token in tokens):
        return 1
    return None


def _keyword_title_option_suffix_re() -> re.Pattern[str]:
    option = r"(?:OPTIONS?(?:[0-9]+)?|\{OPTIONS?(?:[0-9]+)?\})"
    return re.compile(rf"(?:[\s_-]+){option}(?:[\s_-]+{option})*\s*$", re.I)


def _keyword_title_requires_header(text: str, expected_name: str) -> bool:
    """Return whether a normalized anchor needs independent header evidence."""

    rank = _keyword_title_match_rank(text, expected_name)
    if rank is None:
        return False
    candidate = _keyword_title_line_surface(text)
    expected = _keyword_title_line_surface(expected_name)
    if candidate is None or expected is None:
        return False
    if expected.lstrip().startswith("*") and not candidate.lstrip().startswith("*"):
        return True

    candidate_base = candidate
    if rank == 1:
        candidate_base = _keyword_title_option_suffix_re().sub("", candidate_base)

    def presentation_surface(value: str) -> str:
        value = value.strip().casefold()
        return re.sub(r"^\*+\s*", "*", value)

    # The canonical key may equate spaces, underscores, and hyphens, but that
    # equivalence is still an OCR-dependent inference. Require an independent
    # page header whenever the source surface is not already identical.
    return presentation_surface(candidate_base) != presentation_surface(expected)


def _keyword_header_confirms_title(page, expected_name: str) -> bool:
    """Return whether a HeaderBlock independently names the same Keyword.

    The layout detector may omit the literal keyword asterisk from a header,
    so the normalized exact title is the evidence we require here. A header
    with only a family name or an appended running-header suffix does not
    qualify because ``_keyword_title_match_rank`` will not return an exact
    match for it.
    """

    for block in page.blocks:
        if not isinstance(block, HeaderBlock):
            continue
        if _keyword_title_match_rank(block.text, expected_name) != 2:
            continue
        return True
    return False


def _is_strong_keyword_title(text: str, expected_name: str) -> bool:
    return _keyword_title_match_rank(text, expected_name) is not None


def _text_of(block: Block) -> str:
    return block.text.strip() if isinstance(block, TextBlock) else ""


def _is_label(text: str, label: str) -> bool:
    return bool(re.match(rf"^{re.escape(label)}\s*:?(?:\s|$)", text, re.IGNORECASE))


def _is_exact_label(text: str, label: str) -> bool:
    return bool(
        re.fullmatch(
            rf"{re.escape(label)}\s*:?\s*",
            text.strip(),
            re.IGNORECASE,
        )
    )


def _card_label_from_text(text: str) -> str | None:
    match = re.match(
        r"^Card\s+([0-9]+[A-Za-z]?(?:\.[0-9]+[A-Za-z]?)*)(?:\s*[.:]|\s*$)",
        text.strip(),
        re.IGNORECASE,
    )
    return f"Card {match.group(1)}" if match is not None else None


_CARD_CONDITION_RE = re.compile(
    r"(?P<variable>[A-Za-z][A-Za-z0-9_]*)\s*(?:\.\s*)?"
    r"(?P<operator>EQ\.|NE\.|GE\.|GT\.|LE\.|LT\.|=)\s*"
    r"(?P<values>[-+A-Za-z0-9_.]+(?:\s+or\s+[-+A-Za-z0-9_.]+)*)",
    re.IGNORECASE,
)


def _parse_card_conditions(sourced: SourcedBlock) -> list[CardConditionIR]:
    """Extract only explicit condition expressions; retain all source text."""

    if not isinstance(sourced.block, TextBlock):
        return []
    text = sourced.block.text.strip()
    conditions: list[CardConditionIR] = []
    for match in _CARD_CONDITION_RE.finditer(text):
        raw = match.group(0).rstrip(".,;:")
        values = tuple(
            value.strip().rstrip(".,;:")
            for value in re.split(r"\s+or\s+", match.group("values"), flags=re.IGNORECASE)
            if value.strip().rstrip(".,;:")
        )
        if not values:
            continue
        conditions.append(
            CardConditionIR(
                variable=match.group("variable"),
                operator=match.group("operator"),
                values=values,
                raw=raw,
                source_text=text,
                source=sourced.source,
            )
        )
    return conditions


def _append_card_condition_block(card: CardIR, sourced: SourcedBlock) -> None:
    card.condition_blocks.append(sourced)
    card.conditions.extend(_parse_card_conditions(sourced))


def _table_first_row_text(block: TableBlock) -> list[str]:
    rows = table_grid_rows(block)
    if not rows:
        return []
    return [cell.text.strip() for cell in rows[0]]


def _effective_slot_count(
    rows: list[list[Cell]], row_start: int, row_end: int
) -> int:
    """Return the last non-empty slot column in one Card region."""

    width = max((len(row) for row in rows[row_start:row_end]), default=0)
    for column in range(width - 1, 0, -1):
        if any(_cell_text(row, column) is not None for row in rows[row_start:row_end]):
            return column
    return 0


def _sequential_card_slot_count(
    rows: list[list[Cell]], row_start: int, row_end: int
) -> int | None:
    slot_count = _effective_slot_count(rows, row_start, row_end)
    if slot_count == 0:
        return None
    header_slots = [
        _cell_text(rows[row_start], column)
        for column in range(1, slot_count + 1)
    ]
    if header_slots != [str(slot) for slot in range(1, slot_count + 1)]:
        return None
    return slot_count


def _card_regions(
    block: TableBlock, *, allow_structural_labels: bool = False
) -> list[tuple[str, int, int]]:
    """Return Card row regions, preserving all rows in the source table."""

    rows = table_grid_rows(block)
    if rows and len(rows[0]) >= 2:
        first_pair = [
            _normalized_row_label(rows[0][column].text)
            for column in range(2)
        ]
        if first_pair == ["card", "description"]:
            # This is a prose Card-summary table, not a fixed-slot Card
            # definition.  Preserve it as source material without inventing
            # slot or Variable semantics.
            return []

    starts: list[tuple[str, int, bool]] = []
    for row_index, row in enumerate(rows):
        if not row:
            continue
        match = re.fullmatch(
            r"Cards?(?:\s+([0-9]+[A-Za-z]?(?:\.[0-9]+[A-Za-z]?)*))?\s*:?",
            row[0].text.strip(),
            re.IGNORECASE,
        )
        if match is not None:
            starts.append(
                (
                    f"Card {match.group(1)}" if match.group(1) else "Card",
                    row_index,
                    False,
                )
            )
            continue
        if (
            allow_structural_labels
            and row[0].text.strip()
            and _sequential_card_slot_count(rows, row_index, row_index + 1)
            is not None
        ):
            starts.append((row[0].text.strip(), row_index, True))

    starts.sort(key=lambda item: item[1])
    regions: list[tuple[str, int, int]] = []
    for index, (label, start, structural) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(rows)
        if structural:
            variable_rows = _row_indices_by_label(
                block,
                start + 1,
                end,
                {"Variable", "Variable Type", "Variable Type Default"},
            )
            if len(variable_rows) != 1:
                continue
        regions.append((label, start, end))
    return regions


def _cell_text(row: list[Cell], column: int) -> str | None:
    if column >= len(row):
        return None
    value = row[column].text.strip()
    return value or None


def _cell_source(sourced: SourcedBlock, row: int, column: int) -> TableCellSourceRef:
    return TableCellSourceRef(
        document_id=sourced.source.document_id,
        pdf_page=sourced.source.pdf_page,
        manual_page=sourced.source.manual_page,
        block_index=sourced.source.block_index,
        row=row,
        column=column,
    )


def _normalized_row_label(text: str) -> str:
    value = normalize_literal_cell_newlines(text)
    return re.sub(r"\s+", " ", value).strip().casefold()


def _logical_cell_lines(text: str) -> list[str]:
    value = normalize_literal_cell_newlines(text)
    return [line.strip() for line in value.splitlines() if line.strip()]


def _looks_like_field_type(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Z](?:\s*/\s*[A-Z])?", text.strip().upper()))


def _split_variable_type_default_cell(
    text: str,
) -> tuple[str, str, str] | None:
    """Parse one strict ``identifier + type + default`` compressed cell."""

    lines = _logical_cell_lines(text)
    if len(lines) == 1:
        parts = lines[0].split(maxsplit=2)
    else:
        parts = lines
    if len(parts) != 3:
        return None
    variable, field_type, default = (part.strip() for part in parts)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", variable):
        return None
    if not re.fullmatch(r"[A-Z](?:\d+(?:\.\d+)?)?", field_type.upper()):
        return None
    if not default:
        return None
    return variable, field_type, default


def _compressed_row_value(row: list[Cell], slot: int) -> str | None:
    return _cell_text(row, slot - 1)


def _row_indices_by_label(
    block: TableBlock,
    start: int,
    end: int,
    labels: set[str],
) -> list[int]:
    rows = table_grid_rows(block)
    normalized_labels = {_normalized_row_label(label) for label in labels}
    return [
        row_index
        for row_index in range(start, end)
        if rows[row_index]
        and _normalized_row_label(rows[row_index][0].text)
        in normalized_labels
    ]


def _definition_fields(
    sourced: SourcedBlock,
    row_start: int,
    row_end: int,
    issues: list[ParseIssue],
) -> list[CardFieldIR]:
    block = sourced.block
    if not isinstance(block, TableBlock) or row_start >= row_end:
        return []
    rows = table_grid_rows(block)
    semantic_end = next(
        (
            row_index
            for row_index in range(row_start + 1, row_end)
            if _is_variable_description_header_row(rows[row_index])
        ),
        row_end,
    )
    header = rows[row_start]
    slot_count = _effective_slot_count(rows, row_start, semantic_end)
    if slot_count == 0:
        issues.append(
            _issue(
                "CARD_DEFINITION_SLOT_HEADER_INVALID",
                f"definition table at page {sourced.source.pdf_page} has no "
                "Card slot header",
                source=sourced.source,
            )
        )
        return []
    header_slots = [
        _cell_text(header, column) for column in range(1, slot_count + 1)
    ]
    if header_slots != [str(slot) for slot in range(1, slot_count + 1)]:
        issues.append(
            _issue(
                "CARD_DEFINITION_SLOT_HEADER_INVALID",
                f"definition table at page {sourced.source.pdf_page} has a "
                f"non-sequential Card slot header: {header_slots!r}",
                source=sourced.source,
            )
        )

    variable_rows = _row_indices_by_label(
        block,
        row_start + 1,
        semantic_end,
        {"Variable", "Variable Type", "Variable Type Default"},
    )
    type_rows = _row_indices_by_label(
        block, row_start + 1, semantic_end, {"Type"}
    )
    default_rows = _row_indices_by_label(
        block, row_start + 1, semantic_end, {"Default"}
    )
    ambiguous = {
        "Variable": variable_rows,
        "Type": type_rows,
        "Default": default_rows,
    }
    duplicates = [label for label, rows in ambiguous.items() if len(rows) > 1]
    if duplicates:
        issues.append(
            _issue(
                "CARD_DEFINITION_ROW_AMBIGUOUS",
                f"definition table at page {sourced.source.pdf_page} contains "
                f"multiple {'/'.join(duplicates)} rows; the first is retained",
                source=sourced.source,
            )
        )
    variable_row = variable_rows[0] if variable_rows else None
    type_row = type_rows[0] if type_rows else None
    default_row = default_rows[0] if default_rows else None
    if variable_row is None:
        issues.append(
            _issue(
                "CARD_DEFINITION_VARIABLE_ROW_MISSING",
                f"definition table at page {sourced.source.pdf_page} has no "
                "Variable row",
                source=sourced.source,
            )
        )

    combined_labels: set[str] = set()
    split_variables: dict[int, str] = {}
    split_types: dict[int, str] = {}
    split_defaults: dict[int, str] = {}
    if variable_row is not None:
        combined_labels = set(_normalized_row_label(rows[variable_row][0].text).split())
        for slot in range(1, slot_count + 1):
            raw = _cell_text(rows[variable_row], slot)
            if raw is None:
                continue
            compressed_triplet = (
                _split_variable_type_default_cell(raw)
                if combined_labels == {"variable", "type", "default"}
                else None
            )
            if compressed_triplet is not None:
                variable, field_type, default = compressed_triplet
                split_variables[slot] = variable
                split_types[slot] = field_type
                split_defaults[slot] = default
                continue
            parts = _logical_cell_lines(raw)
            if len(parts) == 2 and _looks_like_field_type(parts[1]):
                split_variables[slot] = parts[0]
                split_types[slot] = parts[1]
                continue
            inline = re.fullmatch(
                r"(.+?)\s+([A-Z](?:\s*/\s*[A-Z])?)",
                raw.strip(),
                re.IGNORECASE,
            )
            if (
                inline is not None
                and "type" in combined_labels
                and _looks_like_field_type(inline.group(2))
            ):
                split_variables[slot] = inline.group(1).strip()
                split_types[slot] = inline.group(2).strip()

    compressed_type_row: int | None = None
    compressed_default_row: int | None = None
    candidate_row = variable_row + 1 if variable_row is not None else None
    if (
        type_row is None
        and "type" in combined_labels
        and candidate_row is not None
        and candidate_row < semantic_end
        and len(rows[candidate_row]) <= slot_count
        and all(
            not cell.text.strip() or _looks_like_field_type(cell.text)
            for cell in rows[candidate_row]
        )
    ):
        compressed_type_row = candidate_row
        candidate_row += 1
    if (
        default_row is None
        and "default" in combined_labels
        and candidate_row is not None
        and candidate_row < semantic_end
        and len(rows[candidate_row]) <= slot_count
    ):
        compressed_default_row = candidate_row

    fields: list[CardFieldIR] = []
    for slot in range(1, slot_count + 1):
        variable = split_variables.get(slot)
        if variable is None and variable_row is not None:
            variable = _cell_text(rows[variable_row], slot)
        field_type = (
            _cell_text(rows[type_row], slot) if type_row is not None else None
        )
        if field_type is None:
            field_type = split_types.get(slot)
        if field_type is None and compressed_type_row is not None:
            field_type = _compressed_row_value(
                rows[compressed_type_row], slot
            )
        default = (
            _cell_text(rows[default_row], slot)
            if default_row is not None
            else None
        )
        if default is None:
            default = split_defaults.get(slot)
        if default is None and compressed_default_row is not None:
            default = _compressed_row_value(
                rows[compressed_default_row], slot
            )
        source_row = row_start
        source_column = slot
        for source_candidate, candidate_column in (
            (variable_row, slot),
            (type_row, slot),
            (compressed_type_row, slot - 1),
            (default_row, slot),
            (compressed_default_row, slot - 1),
        ):
            if (
                source_candidate is not None
                and candidate_column < len(rows[source_candidate])
            ):
                source_row = source_candidate
                source_column = candidate_column
                break
        fields.append(
            CardFieldIR(
                slot=slot,
                variable=variable,
                field_type=field_type,
                default=default,
                source=_cell_source(sourced, source_row, source_column),
            )
        )
    return fields


def _is_card_definition_continuation(block: TableBlock) -> bool:
    rows = table_grid_rows(block)
    if not rows or _is_variable_description_table(block):
        return False
    labels = {
        _normalized_row_label(row[0].text)
        for row in rows
        if row and row[0].text.strip()
    }
    return bool(labels) and labels <= {
        "variable",
        "variable type",
        "variable type default",
        "type",
        "default",
    }


def _merge_card_continuation_fields(
    card: CardIR,
    sourced: SourcedBlock,
    issues: list[ParseIssue],
) -> None:
    block = sourced.block
    if not isinstance(block, TableBlock) or not card.fields:
        issues.append(
            _issue(
                "CARD_DEFINITION_CONTINUATION_ORPHAN",
                f"Card continuation table at page {sourced.source.pdf_page} has "
                "no preceding field slots",
                source=sourced.source,
            )
        )
        return
    rows = table_grid_rows(block)
    variable_rows = _row_indices_by_label(
        block,
        0,
        len(rows),
        {"Variable", "Variable Type", "Variable Type Default"},
    )
    type_rows = _row_indices_by_label(block, 0, len(rows), {"Type"})
    default_rows = _row_indices_by_label(block, 0, len(rows), {"Default"})
    variable_row = variable_rows[0] if variable_rows else None
    type_row = type_rows[0] if type_rows else None
    default_row = default_rows[0] if default_rows else None
    merged: list[CardFieldIR] = []
    for field in card.fields:
        slot = field.slot
        variable = (
            _cell_text(rows[variable_row], slot)
            if variable_row is not None
            else None
        )
        field_type = (
            _cell_text(rows[type_row], slot) if type_row is not None else None
        )
        default = (
            _cell_text(rows[default_row], slot)
            if default_row is not None
            else None
        )
        source = field.source
        for row_index, value in (
            (variable_row, variable),
            (type_row, field_type),
            (default_row, default),
        ):
            if row_index is not None and value is not None:
                source = _cell_source(sourced, row_index, slot)
                break
        merged.append(
            CardFieldIR(
                slot=slot,
                variable=field.variable or variable,
                field_type=field.field_type or field_type,
                default=field.default or default,
                source=source,
            )
        )
    card.fields = merged


def _append_card_table(
    keyword: KeywordIR,
    card: CardIR,
    sourced: SourcedBlock,
    *,
    role: Literal["summary", "definition"],
    row_start: int,
    row_end: int,
    continuation_of: BlockSourceRef | None = None,
) -> None:
    if not any(item.source == sourced.source for item in keyword.card_table_blocks):
        keyword.card_table_blocks.append(sourced)
    card.tables.append(
        CardTableIR(
            source_block=sourced,
            role=role,
            row_start=row_start,
            row_end=row_end,
            continuation_of=continuation_of,
        )
    )


def _is_variable_description_header_row(row: list[Cell]) -> bool:
    labels = [
        _normalized_row_label(cell.text) for cell in row if cell.text.strip()
    ]
    return (
        len(labels) == 2
        and labels[0] in {"variable", "ariable"}
        and labels[1] == "description"
    )


def _is_variable_description_table(block: TableBlock) -> bool:
    rows = table_grid_rows(block)
    return bool(rows and _is_variable_description_header_row(rows[0]))


def _option_token(text: str) -> str | None:
    value = text.strip().strip("`")
    if value.upper() in {"<BLANK>", "BLANK", "NONE"}:
        return None
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", value):
        return value
    return None


def _get_card(keyword: KeywordIR, label: str) -> CardIR:
    for card in keyword.cards:
        if card.label.casefold() == label.casefold():
            return card
    card = CardIR(label=label)
    keyword.cards.append(card)
    return card


def _get_option(keyword: KeywordIR, name: str) -> OptionIR:
    for option in keyword.options:
        if option.name.casefold() == name.casefold():
            return option
    option = OptionIR(
        name=name,
        full_name=f"{keyword.name}_{name}",
    )
    keyword.options.append(option)
    return option


def _normalized_variable_name(text: str) -> str:
    value = text.strip().strip("`")
    value = value.replace("^{*}", "*").replace("{*}", "*")
    value = value.replace("\\_", "_").replace("$", "")
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "", value)
    value = re.sub(r"_(?=\d)", "", value)
    return re.sub(r"\s+", "", value).upper()


def table_range_signature(
    table: CardTableIR | VariableDescriptionTableIR,
) -> tuple[tuple[str, ...], ...]:
    """Return a whitespace-normalized signature for exact output deduplication."""

    block = table.source_block.block
    if not isinstance(block, TableBlock):
        return ()
    return tuple(
        tuple(
            re.sub(
                r"\s+",
                " ",
                normalize_literal_cell_newlines(cell.text),
            ).strip()
            for cell in row
        )
        for row in table_grid_rows(block)[table.row_start : table.row_end]
    )


def card_summary_is_redundant(card: CardIR, table: CardTableIR) -> bool:
    """Return true only when a summary row repeats all definition variables."""

    if table.role != "summary" or not card.fields:
        return False
    signature = table_range_signature(table)
    if len(signature) != 1 or len(signature[0]) > len(card.fields):
        return False
    summary_variables = [
        _normalized_variable_name(value) if value else ""
        for value in signature[0]
    ]
    summary_variables.extend([""] * (len(card.fields) - len(summary_variables)))
    definition_variables = [
        _normalized_variable_name(field.variable) if field.variable else ""
        for field in card.fields
    ]
    summary_source = table.source_block.source
    summary_supplied_field = any(
        field.variable
        and field.source.document_id == summary_source.document_id
        and field.source.pdf_page == summary_source.pdf_page
        and field.source.block_index == summary_source.block_index
        for field in card.fields
    )
    return summary_variables == definition_variables and not summary_supplied_field


def _merge_summary_fields(card: CardIR) -> None:
    """Fill definition gaps only when a Card summary provides an explicit slot."""

    if not card.fields:
        return
    for table in card.tables:
        if table.role != "summary":
            continue
        signature = table_range_signature(table)
        if len(signature) != 1:
            continue
        row = signature[0]
        for slot, variable in enumerate(row, start=1):
            if not variable:
                continue
            source = _cell_source(
                table.source_block,
                table.row_start,
                slot - 1,
            )
            while len(card.fields) < slot:
                card.fields.append(
                    CardFieldIR(
                        slot=len(card.fields) + 1,
                        variable=None,
                        field_type=None,
                        default=None,
                        source=source,
                    )
                )
            existing = card.fields[slot - 1]
            if existing.variable is None:
                card.fields[slot - 1] = CardFieldIR(
                    slot=existing.slot,
                    variable=variable,
                    field_type=existing.field_type,
                    default=existing.default,
                    source=source,
                )


class _VariableLookup(dict[str, str]):
    """Catalog lookup with a set of exact-only Card-field aliases."""

    def __init__(self) -> None:
        super().__init__()
        self.exact_only_keys: set[str] = set()


_CARD_FIELD_ALIAS_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\s*(?:/|\bor\b)\s*"
    r"[A-Za-z][A-Za-z0-9_]*)+$",
    re.IGNORECASE,
)


def _card_field_alias_tokens(text: str) -> list[str]:
    """Return exact identifier aliases from one Card field cell.

    Only slash-separated or standalone ``or`` alternatives are accepted.  A
    malformed cell remains a single source variable and never contributes an
    alias.
    """

    value = text.strip()
    if not _CARD_FIELD_ALIAS_RE.fullmatch(value):
        return []
    tokens = [
        token.strip()
        for token in re.split(r"\s*(?:/|\bor\b)\s*", value, flags=re.IGNORECASE)
        if token.strip()
    ]
    if len(tokens) < 2:
        return []
    return tokens


def _variable_lookup(keyword: KeywordIR) -> _VariableLookup:
    lookup = _VariableLookup()
    keyword._catalog_alias_targets = {}
    for variable in keyword.variable_catalog:
        normalized = _normalized_variable_name(variable)
        if not normalized:
            continue
        lookup.setdefault(normalized, variable)

    ambiguous_pages = {
        issue.pdf_page
        for issue in keyword.issues
        if issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
        and issue.pdf_page is not None
    }
    fields_by_source: dict[
        tuple[str, int, int, int | None, int | None], CardFieldIR
    ] = {}
    for card_field in [
        *keyword._catalog_hint_fields,
        *(field for card in keyword.cards for field in card.fields),
    ]:
        if (
            card_field.variable is None
            or card_field.source.pdf_page in ambiguous_pages
        ):
            continue
        source_key = (
            card_field.source.document_id,
            card_field.source.pdf_page,
            card_field.source.block_index,
            card_field.source.row,
            card_field.source.column,
        )
        fields_by_source[source_key] = card_field

    alias_origins: dict[
        str,
        list[
            tuple[
                tuple[str, int, int, int | None, int | None],
                str,
                str,
            ]
        ],
    ] = {}
    for source_key, card_field in fields_by_source.items():
        variable = card_field.variable or ""
        for alias in _card_field_alias_tokens(variable):
            alias_key = _normalized_variable_name(alias)
            if not alias_key:
                continue
            alias_origins.setdefault(alias_key, []).append(
                (source_key, variable, alias)
            )

    # An alias is safe only when it identifies one Card field and is not also
    # an independent catalog variable.  The source field spelling remains in
    # ``variable_catalog``; aliases are lookup-only.
    for alias_key, origins in alias_origins.items():
        if alias_key in lookup or len(origins) != 1:
            continue
        _source_key, source_variable, alias_spelling = origins[0]
        lookup[alias_key] = alias_spelling
        lookup.exact_only_keys.add(alias_key)
        keyword._catalog_alias_targets[alias_key] = [source_variable]
    return lookup


def _catalog_items(lookup: dict[str, str]):
    exact_only = getattr(lookup, "exact_only_keys", set())
    return ((key, value) for key, value in lookup.items() if key not in exact_only)


def _catalog_values(lookup: dict[str, str]):
    return (value for _key, value in _catalog_items(lookup))


def _match_variable(lookup: dict[str, str], text: str) -> str | None:
    normalized = _normalized_variable_name(text)
    exact = lookup.get(normalized)
    if exact is not None:
        return exact
    candidates = [
        variable
        for key, variable in _catalog_items(lookup)
        if len(key) == len(normalized)
        and all(
            left == right or {left, right} == {"0", "O"}
            for left, right in zip(key, normalized)
        )
        and sum(
            {left, right} == {"0", "O"}
            for left, right in zip(key, normalized)
        )
        == 1
    ]
    return candidates[0] if len(candidates) == 1 else None


def _record_confusable_variable_match(
    keyword: KeywordIR,
    source_text: str,
    matched_variable: str | None,
    source_ref: BlockSourceRef,
) -> None:
    if matched_variable is None:
        return
    normalized_source = _normalized_variable_name(source_text)
    target = _normalized_variable_name(matched_variable)
    if normalized_source == target:
        return
    if not (
        len(normalized_source) == len(target)
        and all(
            left == right or {left, right} == {"0", "O"}
            for left, right in zip(normalized_source, target)
        )
    ):
        return
    message = (
        f"variable description title {source_text!r} was associated with "
        f"Card variable {matched_variable!r} by a unique O/0 match"
    )
    if not any(
        issue.code == "VARIABLE_IDENTIFIER_CONFUSABLE_MATCH"
        and issue.message == message
        for issue in keyword.issues
    ):
        keyword.issues.append(
            _issue(
                "VARIABLE_IDENTIFIER_CONFUSABLE_MATCH",
                message,
                severity="info",
                source=source_ref,
            )
        )


def _match_variable_family(
    lookup: dict[str, str],
    text: str,
) -> list[str] | None:
    """Map a documented variable family label to concrete catalog slots."""

    bracket_match = re.fullmatch(
        r"([A-Za-z_]+)\[N\]([A-Za-z_]*)",
        text.strip(),
        re.IGNORECASE,
    )
    if bracket_match is not None:
        prefix = _normalized_variable_name(bracket_match.group(1))
        suffix = _normalized_variable_name(bracket_match.group(2))
        pattern = re.compile(rf"^{re.escape(prefix)}\d+{re.escape(suffix)}$")
        matches = [
            value
            for value in _catalog_values(lookup)
            if pattern.fullmatch(_normalized_variable_name(value))
        ]
        if matches:
            return matches

    axis_family = re.fullmatch(
        r"(?P<prefix>[A-Za-z][A-Za-z_-]*?)(?:\[)?"
        r"(?P<axes>[XYZ](?:\s*,\s*[XYZ])+)\]",
        text.strip(),
        re.IGNORECASE,
    )
    if axis_family is not None:
        prefix = axis_family.group("prefix")
        axes = re.findall(r"[XYZ]", axis_family.group("axes"), re.IGNORECASE)
        matches = [
            _match_variable(lookup, f"{prefix}{axis}") for axis in axes
        ]
        if all(matches):
            return list(dict.fromkeys(matches))

    if re.search(
        r"\bi\s*(?:\^\s*\{?\s*th\s*\}?|th).*\bparameters?\b",
        text,
        re.IGNORECASE,
    ):
        numeric_families: dict[str, list[str]] = {}
        for key, value in _catalog_items(lookup):
            match = re.fullmatch(r"([A-Z_]+)(\d+)", key)
            if match is not None:
                numeric_families.setdefault(match.group(1), []).append(value)
        candidates = [
            values for values in numeric_families.values() if len(values) >= 2
        ]
        if len(candidates) == 1:
            return candidates[0]

    explicit_tokens = [
        token.strip()
        for token in text.split(",")
        if token.strip() and token.strip() != "..."
    ]
    if len(explicit_tokens) >= 2:
        explicit_matches = [_match_variable(lookup, token) for token in explicit_tokens]
        if all(explicit_matches):
            matches = list(dict.fromkeys(explicit_matches))
            if "..." in text:
                normalized = [_normalized_variable_name(value) for value in matches]
                prefixes = {
                    match.group(1)
                    for value in normalized
                    if (match := re.fullmatch(r"([A-Z_]+)\d+", value)) is not None
                }
                if len(prefixes) == 1:
                    prefix = prefixes.pop()
                    matches = [
                        value
                        for value in _catalog_values(lookup)
                        if _normalized_variable_name(value).startswith(prefix)
                        and _normalized_variable_name(value)[len(prefix) :].isdigit()
                    ]
            return matches
        if "..." in text:
            numeric_tokens = [
                re.fullmatch(r"([A-Za-z_]+)(\d+)", _normalized_variable_name(token))
                for token in explicit_tokens
            ]
            prefixes = {
                match.group(1) for match in numeric_tokens if match is not None
            }
            if len(prefixes) == 1 and all(match is not None for match in numeric_tokens):
                prefix = prefixes.pop()
                matches = [
                    value
                    for value in _catalog_values(lookup)
                    if _normalized_variable_name(value).startswith(prefix)
                    and _normalized_variable_name(value)[len(prefix) :].isdigit()
                ]
                if matches:
                    return matches

    cleaned = re.sub(r"[^A-Za-z0-9, ]+", "", text).upper().strip()
    if not cleaned:
        return None
    tokens = [token for token in re.split(r"[, ]+", cleaned) if token]
    prefixes: list[str] = []
    for token in tokens:
        if token.endswith("IJ") and token[:-2].isalpha():
            prefix = token[:-2]
        elif token.endswith(("I", "N")) and token[:-1].isalpha():
            prefix = token[:-1]
        else:
            continue
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    if not prefixes:
        return None
    values = list(_catalog_values(lookup))
    matches = [
        value
        for value in values
        if any(
            _normalized_variable_name(value).startswith(prefix)
            and _normalized_variable_name(value)[len(prefix) :].isdigit()
            for prefix in prefixes
        )
    ]
    return matches or None


def _looks_like_variable_title(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", text.strip()))


def _match_variable_table_heading(
    lookup: dict[str, str],
    text: str,
) -> str | None:
    matched = _match_variable(lookup, text)
    if matched is not None:
        return matched
    math_text = text.strip()
    if math_text.startswith("$") and math_text.endswith("$"):
        if not re.search(
            r"(?:=|\+|/|\\(?:times|frac|left|right)\b)", math_text
        ):
            candidate = math_text.strip("$").strip().rstrip(",;:").strip()
            candidate = re.sub(r"\\(?:mathrm|rm)\b", "", candidate)
            candidate = candidate.replace("{", "").replace("}", "")
            candidate = candidate.replace("_", "").replace("^", "")
            candidate = re.sub(r"\s+", "", candidate)
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", candidate):
                matched = _match_variable(lookup, candidate)
                if matched is not None:
                    return matched
    value = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    value = re.sub(r"\s+(?:values?|attributes?)\s*$", "", value, flags=re.IGNORECASE)
    return _match_variable(lookup, value)


def _match_leading_variable(
    lookup: dict[str, str],
    text: str,
) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    first_line = lines[0]
    if len(lines) > 1 and re.fullmatch(
        r"VARIABLE\s*:?\s*", first_line, re.IGNORECASE
    ):
        matched = _match_variable_table_heading(lookup, lines[1])
        if matched is not None:
            return matched
    combined_header = re.fullmatch(
        r"VARIABLE\s+(.+)", first_line, re.IGNORECASE
    )
    if combined_header is not None:
        matched = _match_variable_table_heading(
            lookup, combined_header.group(1).strip()
        )
        if matched is not None:
            return matched
    first_token = re.match(r"^([^\s:]+)(?:\s+|:\s*)", first_line)
    if first_token is not None:
        matched = _match_variable_table_heading(lookup, first_token.group(1))
        if matched is not None:
            return matched
    spaced_identifier = re.match(
        r"^((?:[A-Za-z]\s+){1,15}[A-Za-z])\s+\S",
        first_line,
    )
    if spaced_identifier is not None:
        return _match_variable_table_heading(
            lookup, re.sub(r"\s+", "", spaced_identifier.group(1))
        )
    if len(lines) > 1:
        return _match_variable_table_heading(lookup, lines[0])
    return None


def _match_leading_variable_group(
    lookup: dict[str, str],
    text: str,
) -> tuple[str, list[str]] | None:
    """Match catalog-backed comma/slash groups at the start of text lines."""

    targets: list[str] = []
    source_tokens: list[str] = []
    for line_index, line in enumerate(text.splitlines()):
        before_eq = re.split(
            r"\b(?:EQ|NE|GE|GT|LE|LT)\.", line, maxsplit=1, flags=re.IGNORECASE
        )[0]
        value = before_eq.strip()
        if line_index > 0 and value.startswith("/"):
            value = value[1:].lstrip()
        token_match = re.match(r"[A-Za-z][A-Za-z0-9_-]*", value)
        if token_match is None:
            continue
        position = token_match.end()
        token = token_match.group(0)
        matched = _match_variable(lookup, token)
        if matched is None:
            continue
        line_targets = [matched]
        line_tokens = [token]
        while True:
            separator = re.match(r"\s*[,/]\s*", value[position:])
            if separator is None:
                break
            next_start = position + separator.end()
            next_token = re.match(
                r"[A-Za-z][A-Za-z0-9_-]*", value[next_start:]
            )
            if next_token is None:
                break
            candidate = next_token.group(0)
            candidate_match = _match_variable(lookup, candidate)
            if candidate_match is None:
                break
            line_targets.append(candidate_match)
            line_tokens.append(candidate)
            position = next_start + next_token.end()
        remainder = value[position:].lstrip()
        has_group_shape = len(line_targets) > 1 or (
            len(line_targets) == 1
            and re.match(r"^(?:[,/]|$)", remainder) is not None
        )
        has_eq_boundary = bool(
            re.search(r"\b(?:EQ|NE|GE|GT|LE|LT)\.", line, re.IGNORECASE)
        )
        if len(line_targets) == 1 and not (has_group_shape or has_eq_boundary):
            continue
        for source_token, target in zip(line_tokens, line_targets, strict=True):
            if target not in targets:
                targets.append(target)
                source_tokens.append(source_token)
    if len(targets) < 2:
        return None
    return ", ".join(source_tokens), targets


def _is_description_header_row(row: list[Cell]) -> bool:
    return len(row) >= 2 and _normalized_row_label(row[1].text) == "description"


def _is_variable_continuation_label(text: str) -> bool:
    value = text.strip()
    if not value:
        return True
    normalized = _normalized_row_label(value)
    if normalized in {
        "variable",
        "variables",
        "description",
        "attribute",
        "attributes",
        "operation",
        "default",
        "type",
    }:
        return True
    return bool(
        re.match(r"^(?:EQ|NE|GE|GT|LE|LT)\.", value, re.IGNORECASE)
        or re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value)
        or re.match(r"^(?:[<>]=?|[=≠])\s*[-+]?\d", value)
    )


def _referenced_catalog_variable(
    lookup: dict[str, str],
    row: list[Cell],
) -> str | None:
    text = " ".join(cell.text for cell in row[1:])
    matches: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text):
        matched = lookup.get(_normalized_variable_name(token))
        if matched is not None and matched not in matches:
            matches.append(matched)
    return matches[0] if len(matches) == 1 else None


def _refresh_variable_catalog(keyword: KeywordIR) -> None:
    seen_variables: set[str] = set()
    keyword.variable_catalog = []
    for variable in keyword._catalog_hints:
        normalized = variable.strip()
        key = normalized.casefold()
        if normalized and key not in seen_variables:
            seen_variables.add(key)
            keyword.variable_catalog.append(normalized)
    for card in keyword.cards:
        for field in card.fields:
            if field.variable is None:
                continue
            normalized = field.variable.strip()
            key = normalized.casefold()
            if key not in seen_variables:
                seen_variables.add(key)
                keyword.variable_catalog.append(normalized)


def _preseed_variable_catalog(keyword: KeywordIR) -> None:
    """Collect exact Card variables before the ordered semantic pass.

    Manuals can place a Card definition after its Variable Description.  This
    prepass exposes only variables from the same fixed-slot Card structures
    accepted by the main classifier; it does not classify or rewrite blocks.
    """

    hints: list[str] = []
    hint_fields: list[CardFieldIR] = []
    seen: set[str] = set()
    for sourced in keyword.unclassified_blocks:
        block = sourced.block
        if not isinstance(block, TableBlock):
            continue
        page_has_ambiguous_keyword_boundary = any(
            issue.pdf_page == sourced.source.pdf_page
            and issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
            for issue in keyword.issues
        )
        for _label, row_start, row_end in _card_regions(
            block,
            allow_structural_labels=not page_has_ambiguous_keyword_boundary,
        ):
            for field in _definition_fields(
                sourced, row_start, row_end, issues=[]
            ):
                if field.variable is None:
                    continue
                hint_fields.append(field)
                variable = field.variable.strip()
                key = variable.casefold()
                if variable and key not in seen:
                    seen.add(key)
                    hints.append(variable)
    keyword._catalog_hints = hints
    keyword._catalog_hint_fields = hint_fields
    _refresh_variable_catalog(keyword)


def _get_variable_description(
    keyword: KeywordIR,
    variable: str,
    applies_to: list[str] | None = None,
) -> VariableDescriptionIR:
    alias_targets = keyword._catalog_alias_targets.get(
        _normalized_variable_name(variable)
    )
    if alias_targets is not None and (
        applies_to is None or applies_to == [variable]
    ):
        applies_to = alias_targets
    for description in keyword.variable_descriptions:
        if description.variable.casefold() == variable.casefold():
            if applies_to:
                for target in applies_to:
                    if target.casefold() not in {
                        item.casefold() for item in description.applies_to
                    }:
                        description.applies_to.append(target)
            return description
    description = VariableDescriptionIR(
        variable=variable,
        applies_to=list(applies_to or [variable]),
    )
    keyword.variable_descriptions.append(description)
    return description


def _append_variable_table(
    keyword: KeywordIR,
    sourced: SourcedBlock,
    current_variable: str | None,
) -> str | None:
    """Index variable rows while retaining the raw TableBlock exactly once."""

    block = sourced.block
    if not isinstance(block, TableBlock):
        return current_variable
    if not any(
        item.source == sourced.source for item in keyword.variable_description_blocks
    ):
        keyword.variable_description_blocks.append(sourced)

    lookup = _variable_lookup(keyword)
    has_generic_header = _is_variable_description_table(block)
    header_end = 1 if has_generic_header else 0

    rows = table_grid_rows(block)
    if not has_generic_header and rows and _is_description_header_row(
        rows[0]
    ):
        heading = _match_variable_table_heading(
            lookup, rows[0][0].text.strip()
        )
        if heading is not None:
            description = _get_variable_description(keyword, heading)
            description.tables.append(
                VariableDescriptionTableIR(
                    source_block=sourced,
                    row_start=0,
                    row_end=len(rows),
                )
            )
            return heading

    if current_variable is None and header_end < len(rows):
        first_row = rows[header_end]
        first_label = first_row[0].text.strip() if first_row else ""
        if (
            _is_variable_continuation_label(first_label)
            and keyword.variable_descriptions
        ):
            current_variable = keyword.variable_descriptions[-1].variable
        elif _is_variable_continuation_label(first_label):
            current_variable = _referenced_catalog_variable(lookup, first_row)

    active_variable = current_variable
    active_targets: list[str] | None = None
    active_start: int | None = None
    active_continuation_of: BlockSourceRef | None = None
    previous_source: BlockSourceRef | None = None
    orphan_reported = False
    missing_catalog_reported = False
    if current_variable is not None:
        previous = _get_variable_description(keyword, current_variable)
        if previous.tables:
            previous_source = previous.tables[-1].source_block.source

    def flush(end: int) -> None:
        nonlocal active_start, active_continuation_of
        if (
            active_variable is not None
            and active_start is not None
            and active_start < end
        ):
            _get_variable_description(
                keyword, active_variable, applies_to=active_targets
            ).tables.append(
                VariableDescriptionTableIR(
                    source_block=sourced,
                    row_start=active_start,
                    row_end=end,
                    continuation_of=active_continuation_of,
                )
            )
        active_start = None
        active_continuation_of = None

    for row_index in range(header_end, len(rows)):
        row = rows[row_index]
        label = row[0].text.strip() if row else ""
        if label:
            matched_variable = _match_variable_table_heading(lookup, label)
            _record_confusable_variable_match(
                keyword, label, matched_variable, sourced.source
            )
            family_variables = (
                None
                if matched_variable is not None
                else _match_variable_family(lookup, label)
            )
            inferred_without_catalog = (
                has_generic_header
                and not lookup
                and _looks_like_variable_title(label)
            )
            if inferred_without_catalog:
                matched_variable = label
                if not missing_catalog_reported:
                    keyword.issues.append(
                        _issue(
                            "VARIABLE_DESCRIPTION_CATALOG_UNAVAILABLE",
                            "variable descriptions were indexed from an explicit "
                            "VARIABLE/DESCRIPTION table because no Card variable "
                            "catalog was available",
                            source=sourced.source,
                        )
                    )
                    missing_catalog_reported = True
            if matched_variable is None and family_variables is None:
                if _is_variable_continuation_label(label):
                    if active_variable is not None:
                        if active_start is None:
                            active_start = row_index
                            active_continuation_of = previous_source
                    elif not orphan_reported:
                        keyword.issues.append(
                            _issue(
                                "VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN",
                                "variable description continuation rows have no "
                                "matched variable title",
                                source=sourced.source,
                            )
                        )
                        orphan_reported = True
                    continue
                if active_variable is not None and not _looks_like_variable_title(
                    label
                ):
                    # Math/ellipsis cells are often OCR continuation markers.
                    if active_start is None:
                        active_start = row_index
                        active_continuation_of = previous_source
                    continue
            if matched_variable is None and family_variables is None:
                flush(row_index)
                active_variable = None
                active_targets = None
                keyword.issues.append(
                    _issue(
                        "VARIABLE_DESCRIPTION_UNMATCHED_TITLE",
                        f"variable description title {label!r} is not present in "
                        "the Card variable catalog",
                        source=sourced.source,
                    )
                )
                continue
            flush(row_index)
            active_variable = matched_variable or (
                label if family_variables is not None else None
            )
            active_targets = family_variables or (
                [matched_variable] if matched_variable is not None else None
            )
            active_start = row_index
            active_continuation_of = None
            continue
        if active_variable is None:
            if not orphan_reported:
                keyword.issues.append(
                    _issue(
                        "VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN",
                        "variable description continuation rows have no matched "
                        "variable title",
                        source=sourced.source,
                    )
                )
                orphan_reported = True
        elif active_start is None:
            active_start = row_index
            active_continuation_of = previous_source
    flush(len(rows))
    return active_variable


def _classify_strong_semantics(keyword: KeywordIR) -> None:
    """Move only strongly anchored blocks out of the unclassified stream."""

    original = list(keyword.unclassified_blocks)
    keyword.unclassified_blocks = []
    state = "general"
    pending_card_label: str | None = None
    last_definition_card_label: str | None = None
    variable_region = False
    current_variable: str | None = None
    title_assigned = False

    for sourced in original:
        block = sourced.block
        text = _text_of(block)

        if text and _is_exact_label(text, "References"):
            state = "references"
            pending_card_label = None
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            keyword.references_blocks.append(sourced)
            continue
        if text and _is_exact_label(text, "Remarks"):
            state = "remarks"
            pending_card_label = None
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            keyword.remarks_blocks.append(sourced)
            continue
        if state == "references":
            keyword.references_blocks.append(sourced)
            continue
        if state == "remarks":
            keyword.remarks_blocks.append(sourced)
            continue

        if (
            not title_assigned
            and isinstance(block, TextBlock)
            and _is_strong_keyword_title(text, keyword.name)
        ):
            keyword.title_blocks.append(sourced)
            title_assigned = True
            continue

        if text and _is_label(text, "Purpose"):
            state = "purpose"
            pending_card_label = None
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            keyword.purpose_blocks.append(sourced)
            continue

        if text and re.fullmatch(
            r"(?:For\s+\S+\s+)?(?:The\s+)?available\s+options?\s+"
            r"(?:are|is|include|includes)\s*:?\s*",
            text,
            re.IGNORECASE,
        ):
            state = "options"
            pending_card_label = None
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            keyword.option_intro_blocks.append(sourced)
            continue

        if text and re.fullmatch(r"Card\s+Summary\s*:?\s*", text, re.IGNORECASE):
            state = "cards"
            pending_card_label = None
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            keyword.card_intro_blocks.append(sourced)
            continue
        if text and re.fullmatch(
            r"Data\s+Card\s+Definitions\s*:?\s*", text, re.IGNORECASE
        ):
            state = "cards"
            pending_card_label = None
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            keyword.card_intro_blocks.append(sourced)
            continue

        card_label = _card_label_from_text(text) if text else None
        if card_label is not None:
            state = "cards"
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            pending_card_label = card_label
            _append_card_condition_block(_get_card(keyword, card_label), sourced)
            continue

        if text and re.match(r"^.+\s+Card\.\s+", text, re.IGNORECASE):
            state = "cards"
            pending_card_label = None
            variable_region = False
            current_variable = None
            last_definition_card_label = None
            keyword.card_intro_blocks.append(sourced)
            continue

        if isinstance(block, TableBlock):
            page_has_ambiguous_keyword_boundary = any(
                issue.pdf_page == sourced.source.pdf_page
                and issue.code == "KEYWORD_BOUNDARY_AMBIGUOUS"
                for issue in keyword.issues
            )
            regions = _card_regions(
                block,
                allow_structural_labels=not page_has_ambiguous_keyword_boundary,
            )
            if regions:
                state = "cards"
                variable_region = False
                pending_card_label = None
                current_variable = None
                last_definition_card_label = None
                for table_card_label, row_start, row_end in regions:
                    card = _get_card(keyword, table_card_label)
                    _append_card_table(
                        keyword,
                        card,
                        sourced,
                        role="definition",
                        row_start=row_start,
                        row_end=row_end,
                    )
                    card.fields.extend(
                        _definition_fields(sourced, row_start, row_end, keyword.issues)
                    )
                    _merge_summary_fields(card)
                    last_definition_card_label = table_card_label
                _refresh_variable_catalog(keyword)
                continue
            if pending_card_label is not None:
                card = _get_card(keyword, pending_card_label)
                _append_card_table(
                    keyword,
                    card,
                    sourced,
                    role="summary",
                    row_start=0,
                    row_end=len(table_grid_rows(block)),
                )
                _merge_summary_fields(card)
                pending_card_label = None
                continue
            if _is_variable_description_table(block):
                state = "variables"
                variable_region = True
                pending_card_label = None
                last_definition_card_label = None
                current_variable = _append_variable_table(
                    keyword, sourced, current_variable
                )
                continue
            if (
                state == "cards"
                and last_definition_card_label is not None
                and _is_card_definition_continuation(block)
            ):
                card = _get_card(keyword, last_definition_card_label)
                previous_source = (
                    card.tables[-1].source_block.source if card.tables else None
                )
                _append_card_table(
                    keyword,
                    card,
                    sourced,
                    role="definition",
                    row_start=0,
                    row_end=len(table_grid_rows(block)),
                    continuation_of=previous_source,
                )
                _merge_card_continuation_fields(card, sourced, keyword.issues)
                _refresh_variable_catalog(keyword)
                continue

        if state == "cards" and text:
            lookup = _variable_lookup(keyword)
            grouped = _match_leading_variable_group(lookup, text)
            if grouped is not None:
                group_label, group_variables = grouped
                state = "variables"
                variable_region = True
                pending_card_label = None
                last_definition_card_label = None
                current_variable = group_label
                _get_variable_description(
                    keyword,
                    group_label,
                    applies_to=group_variables,
                ).blocks.append(sourced)
                continue
            matched_variable = _match_variable_table_heading(lookup, text)
            if matched_variable is None:
                matched_variable = _match_leading_variable(lookup, text)
            if matched_variable is not None:
                _record_confusable_variable_match(
                    keyword, text, matched_variable, sourced.source
                )
                state = "variables"
                variable_region = True
                pending_card_label = None
                last_definition_card_label = None
                current_variable = matched_variable
                _get_variable_description(
                    keyword, matched_variable
                ).blocks.append(sourced)
                continue

        if text and (
            _is_exact_label(text, "VARIABLE")
            or _is_exact_label(text, "DESCRIPTION")
        ):
            state = "variables"
            entering_variable_region = not variable_region
            variable_region = True
            pending_card_label = None
            if entering_variable_region:
                current_variable = None
            last_definition_card_label = None
            keyword.variable_description_blocks.append(sourced)
            continue

        if state == "purpose":
            keyword.purpose_blocks.append(sourced)
            continue
        if state == "options":
            option_name = _option_token(text) if text else None
            if option_name is None:
                keyword.option_intro_blocks.append(sourced)
            else:
                _get_option(keyword, option_name).blocks.append(sourced)
            continue
        if variable_region:
            if isinstance(block, TableBlock):
                current_variable = _append_variable_table(
                    keyword, sourced, current_variable
                )
                continue
            if text and _normalized_title_line(text) == _normalized_title_line(
                keyword.name.split("_", 1)[0]
            ):
                # OCR may expose a repeated volume/family header as a text block.
                keyword.variable_description_blocks.append(sourced)
                continue
            lookup = _variable_lookup(keyword)
            matched_variable = None
            if text:
                grouped = _match_leading_variable_group(lookup, text)
                if grouped is not None:
                    group_label, group_variables = grouped
                    current_variable = group_label
                    _get_variable_description(
                        keyword,
                        group_label,
                        applies_to=group_variables,
                    ).blocks.append(sourced)
                    continue
                matched_variable = _match_variable_table_heading(lookup, text)
                if matched_variable is None:
                    matched_variable = _match_leading_variable(lookup, text)
            elif isinstance(block, MathBlock):
                matched_variable = _match_variable_table_heading(
                    lookup, block.text
                )
            if text:
                _record_confusable_variable_match(
                    keyword, text, matched_variable, sourced.source
                )
            if matched_variable is not None:
                current_variable = matched_variable
                _get_variable_description(keyword, matched_variable).blocks.append(
                    sourced
                )
            else:
                family_variables = (
                    _match_variable_family(lookup, text) if text else None
                )
                if family_variables is not None:
                    current_variable = text.strip()
                    _get_variable_description(
                        keyword,
                        current_variable,
                        applies_to=family_variables,
                    ).blocks.append(sourced)
                    continue
                if current_variable is not None:
                    _get_variable_description(keyword, current_variable).blocks.append(
                        sourced
                    )
                    continue
                keyword.variable_description_blocks.append(sourced)
                if text and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,31}", text):
                    keyword.issues.append(
                        _issue(
                            "VARIABLE_DESCRIPTION_UNMATCHED_TITLE",
                            f"variable description title {text!r} is not present in "
                            "the Card variable catalog",
                            source=sourced.source,
                        )
                    )
            continue
        if state == "cards" and pending_card_label is not None:
            _append_card_condition_block(
                _get_card(keyword, pending_card_label), sourced
            )
            continue
        if state == "cards":
            keyword.card_intro_blocks.append(sourced)
            continue
        if state == "general" and title_assigned:
            keyword.description_blocks.append(sourced)
            continue
        keyword.unclassified_blocks.append(sourced)

    _refresh_variable_catalog(keyword)


def _title_anchor_candidates(
    section: SectionIR, pdf_page: int
) -> list[tuple[int, int, str]]:
    page = next((item for item in section.pages if item.pdf_page == pdf_page), None)
    if page is None:
        return []
    candidates: list[tuple[int, int, str]] = []
    for index, block in enumerate(page.blocks):
        if not isinstance(block, TextBlock):
            continue
        rank = _keyword_title_match_rank(block.text, section.name)
        key = _normalized_title_line(block.text)
        if rank is not None and key is not None:
            candidates.append((index, rank, key))
    return candidates


def _title_anchor_indices(section: SectionIR, pdf_page: int) -> list[int]:
    """Compatibility helper retained for focused tests and callers."""

    return [index for index, _rank, _key in _title_anchor_candidates(section, pdf_page)]


def _select_title_anchor(
    section: SectionIR, pdf_page: int
) -> tuple[int, str] | None:
    """Select one strong anchor without accepting fuzzy or distant duplicates."""

    page = _page_for(section, pdf_page)
    if page is None:
        return None
    candidates = [
        candidate
        for candidate in _title_anchor_candidates(section, pdf_page)
        if not _keyword_title_requires_header(
            page.blocks[candidate[0]].text, section.name
        )
        or _keyword_header_confirms_title(page, section.name)
    ]
    if not candidates:
        return None
    best_rank = max(rank for _index, rank, _key in candidates)
    best = [item for item in candidates if item[1] == best_rank]
    if len(best) == 1:
        index, _rank, key = best[0]
        return index, key

    # A repeated title generated by the layout/OCR pass is safe to collapse
    # only when the equivalent anchors form one contiguous run.  Non-adjacent
    # duplicates remain ambiguous because either could be the true boundary.
    keys = {key for _index, _rank, key in best}
    indices = sorted(index for index, _rank, _key in best)
    if len(keys) != 1:
        return None
    for left, right in zip(indices, indices[1:]):
        for block in page.blocks[left + 1 : right]:
            if isinstance(block, (HeaderBlock, FooterBlock)):
                continue
            if isinstance(block, TextBlock) and not block.text.strip():
                continue
            return None
    return indices[0], next(iter(keys))


def _page_for(section: SectionIR, pdf_page: int):
    return next((page for page in section.pages if page.pdf_page == pdf_page), None)


def _boundary_slices(
    sections: list[SectionIR],
) -> tuple[dict[tuple[int, int], tuple[int, int]], dict[int, list[ParseIssue]]]:
    """Resolve conventional two-section shared pages using title anchors."""

    slices: dict[tuple[int, int], tuple[int, int]] = {}
    issues: dict[int, list[ParseIssue]] = {index: [] for index in range(len(sections))}
    page_owners: dict[tuple[str, int], list[int]] = {}
    for index, section in enumerate(sections):
        for source_page in section.source_pages:
            page_owners.setdefault(
                (section.document_id, source_page.pdf_page), []
            ).append(index)

    for (document_id, pdf_page), owner_indices in page_owners.items():
        if len(owner_indices) <= 1:
            continue
        owners = sorted(owner_indices)
        conventional_shape = bool(owners)
        for position, owner_index in enumerate(owners):
            owner = sections[owner_index]
            if not owner.source_pages:
                conventional_shape = False
                break
            if position == 0:
                conventional_shape &= owner.source_pages[-1].pdf_page == pdf_page
            elif position == len(owners) - 1:
                conventional_shape &= owner.source_pages[0].pdf_page == pdf_page
            else:
                conventional_shape &= (
                    owner.source_pages[0].pdf_page == pdf_page
                    and owner.source_pages[-1].pdf_page == pdf_page
                )

        page = _page_for(sections[owners[-1]], pdf_page) if owners else None
        selected: list[tuple[int, int, str]] = []
        if conventional_shape and page is not None:
            for owner_index in owners[1:]:
                selected_anchor = _select_title_anchor(
                    sections[owner_index], pdf_page
                )
                if selected_anchor is None:
                    selected = []
                    break
                selected.append((owner_index, *selected_anchor))
            if selected and all(
                left[1] < right[1]
                for left, right in zip(selected, selected[1:])
            ):
                boundaries = [0, *(item[1] for item in selected), len(page.blocks)]
                slices_have_content = True
                for position, owner_index in enumerate(owners):
                    owner = sections[owner_index]
                    if len(owner.pages) != 1:
                        continue
                    start, end = boundaries[position], boundaries[position + 1]
                    meaningful = 0
                    for block_index in range(start, end):
                        block = page.blocks[block_index]
                        if isinstance(block, (HeaderBlock, FooterBlock)):
                            continue
                        if (
                            position > 0
                            and block_index == start
                            and isinstance(block, TextBlock)
                            and _is_strong_keyword_title(block.text, owner.name)
                        ):
                            continue
                        meaningful += 1
                    if meaningful == 0:
                        slices_have_content = False
                        break
                if not slices_have_content:
                    selected = []
            if selected and all(
                left[1] < right[1]
                for left, right in zip(selected, selected[1:])
            ):
                boundaries = [0, *(item[1] for item in selected), len(page.blocks)]
                anchor_summary = ", ".join(
                    f"{anchor} ({sections[owner_index].name})"
                    for owner_index, anchor, _key in selected
                )
                for position, owner_index in enumerate(owners):
                    slices[(owner_index, pdf_page)] = (
                        boundaries[position],
                        boundaries[position + 1],
                    )
                    message = (
                        f"shared PDF page {pdf_page} was split using ordered "
                        f"strong Keyword title anchors at {anchor_summary}"
                    )
                    source = next(
                        page_ref
                        for page_ref in sections[owner_index].source_pages
                        if page_ref.pdf_page == pdf_page
                    )
                    issues[owner_index].append(
                        _issue(
                            "KEYWORD_BOUNDARY_RESOLVED",
                            message,
                            severity="info",
                            source=source,
                        )
                    )
                continue

        owner_names = ", ".join(sections[index].section_id for index in owners)
        message = (
            f"shared PDF page {pdf_page} could not be split using one strong "
            f"Keyword title anchor; preserving content for {owner_names}"
        )
        for index in owners:
            source = next(
                page
                for page in sections[index].source_pages
                if page.pdf_page == pdf_page
            )
            issues[index].append(
                _issue("KEYWORD_BOUNDARY_AMBIGUOUS", message, source=source)
            )
    return slices, issues


def _build_block_stream(
    section: SectionIR,
    section_index: int,
    slices: dict[tuple[int, int], tuple[int, int]],
) -> BlockStream:
    stream = BlockStream()
    for page in sorted(section.pages, key=lambda item: item.pdf_page):
        start, end = slices.get(
            (section_index, page.pdf_page), (0, len(page.blocks))
        )
        for block_index in range(start, end):
            block = page.blocks[block_index]
            source = BlockSourceRef(
                document_id=section.document_id,
                pdf_page=page.pdf_page,
                manual_page=page.manual_page,
                block_index=block_index,
            )
            sourced = SourcedBlock(source=source, block=block)
            stream.owned_sources.append(source)
            if isinstance(block, (HeaderBlock, FooterBlock)):
                stream.ignored_blocks.append(sourced)
            else:
                stream.content_blocks.append(sourced)
    return stream


def validate_keyword_ir(keyword: KeywordIR) -> list[ParseIssue]:
    """Verify that every owned source block is assigned exactly once."""

    expected = set(keyword.owned_sources)
    accounted = [block.source for block in keyword.accounted_blocks()]
    accounted_set = set(accounted)
    issues: list[ParseIssue] = []
    if len(accounted) != len(accounted_set):
        issues.append(
            _issue(
                "KEYWORD_BLOCK_ASSIGNED_MULTIPLE_TIMES",
                "one or more source blocks are assigned to multiple KeywordIR fields",
                severity="error",
            )
        )
    if expected != accounted_set:
        missing = len(expected - accounted_set)
        unexpected = len(accounted_set - expected)
        issues.append(
            _issue(
                "KEYWORD_BLOCK_ACCOUNTING_MISMATCH",
                f"KeywordIR block accounting mismatch: missing={missing}, "
                f"unexpected={unexpected}",
                severity="error",
            )
        )
    return issues


def reconstruct_keywords(sections: list[SectionIR]) -> list[KeywordIR]:
    """Create conservative KeywordIR objects from ordered keyword sections."""

    keyword_sections = [section for section in sections if section.kind == "keyword"]
    slices, boundary_issues = _boundary_slices(keyword_sections)
    keywords: list[KeywordIR] = []
    for index, section in enumerate(keyword_sections):
        stream = _build_block_stream(section, index, slices)
        issues = [
            issue
            for issue in section.issues
            if issue.code != "SECTION_SHARED_BOUNDARY_PAGE"
        ]
        issues.extend(boundary_issues[index])
        keyword = KeywordIR(
            document_id=section.document_id,
            section_id=section.section_id,
            keyword_id=section.keyword_id or section.section_id,
            name=section.name,
            volume=section.volume,
            legacy_ids=list(section.legacy_ids),
            source_pages=list(section.source_pages),
            owned_sources=stream.owned_sources,
            options=[
                OptionIR(name=option, full_name=f"{section.name}_{option}")
                for option in section.options
            ],
            unclassified_blocks=stream.content_blocks,
            ignored_blocks=stream.ignored_blocks,
            issues=issues,
        )
        _preseed_variable_catalog(keyword)
        _classify_strong_semantics(keyword)
        keyword.issues.extend(validate_keyword_ir(keyword))
        if not keyword.content_blocks():
            keyword.status = "failed"
            if section.pages:
                keyword.issues.append(
                    _issue(
                        "KEYWORD_CONTENT_EMPTY",
                        "Keyword candidate contains no non-header/footer blocks",
                        severity="error",
                    )
                )
        elif any(issue.severity in {"warning", "error"} for issue in keyword.issues):
            keyword.status = "warning"
        else:
            keyword.status = "success"
        keywords.append(keyword)
    return keywords

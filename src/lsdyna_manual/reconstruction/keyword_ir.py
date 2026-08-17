"""Keyword-level IR with block provenance and conservative boundary slicing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from lsdyna_manual.parser.page_ir import (
    Block,
    Cell,
    FooterBlock,
    HeaderBlock,
    ParseIssue,
    TableBlock,
    TextBlock,
)
from lsdyna_manual.reconstruction.section_ir import SectionIR, SectionSourcePage


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
        blocks = list(self.purpose_blocks)
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


def _issue(code: str, message: str, *, severity: str = "warning") -> ParseIssue:
    return ParseIssue(severity=severity, code=code, message=message)


def _normalized_title_line(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    line = re.sub(r"^#{1,6}\s*", "", lines[0]).strip()
    line = line.strip("`").strip()
    if line.startswith("**") and line.endswith("**") and len(line) > 4:
        line = line[2:-2].strip()
    line = line.replace("^{*}", "*").replace("{*}", "*")
    line = line.replace("\\_", "_").replace("$", "").replace("^", "")
    line = line.replace("{", "").replace("}", "")
    return re.sub(r"\s+", "", line).upper()


def _is_strong_keyword_title(text: str, expected_name: str) -> bool:
    candidate = _normalized_title_line(text)
    expected = _normalized_title_line(expected_name)
    if candidate is None or expected is None:
        return False
    accepted = {
        expected,
        f"{expected}_OPTION",
        f"{expected}_OPTIONS",
        f"{expected}_{{OPTION}}",
        f"{expected}_{{OPTIONS}}",
        f"{expected}_(OPTION)",
        f"{expected}_(OPTIONS)",
    }
    return candidate in accepted


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
    match = re.match(r"^Card\s+([0-9]+[A-Za-z]?)\s*[.:]", text.strip(), re.IGNORECASE)
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
    if not block.rows:
        return []
    return [cell.text.strip() for cell in block.rows[0]]


def _card_regions(block: TableBlock) -> list[tuple[str, int, int]]:
    """Return Card row regions, preserving all rows in the source table."""

    starts: list[tuple[str, int]] = []
    for row_index, row in enumerate(block.rows):
        if not row:
            continue
        match = re.fullmatch(
            r"Card\s+([0-9]+[A-Za-z]?)\s*:?", row[0].text.strip(), re.IGNORECASE
        )
        if match is not None:
            starts.append((f"Card {match.group(1)}", row_index))
    return [
        (
            label,
            start,
            starts[index + 1][1] if index + 1 < len(starts) else len(block.rows),
        )
        for index, (label, start) in enumerate(starts)
    ]


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


def _row_indices_by_label(
    block: TableBlock,
    start: int,
    end: int,
    labels: set[str],
) -> list[int]:
    normalized_labels = {label.casefold() for label in labels}
    return [
        row_index
        for row_index in range(start, end)
        if block.rows[row_index]
        and block.rows[row_index][0].text.strip().casefold() in normalized_labels
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
    header = block.rows[row_start]
    slot_count = max(0, len(header) - 1)
    if slot_count == 0:
        issues.append(
            _issue(
                "CARD_DEFINITION_SLOT_HEADER_INVALID",
                f"definition table at page {sourced.source.pdf_page} has no "
                "Card slot header",
            )
        )
        return []
    header_slots = [_cell_text(header, column) for column in range(1, len(header))]
    if header_slots != [str(slot) for slot in range(1, slot_count + 1)]:
        issues.append(
            _issue(
                "CARD_DEFINITION_SLOT_HEADER_INVALID",
                f"definition table at page {sourced.source.pdf_page} has a "
                f"non-sequential Card slot header: {header_slots!r}",
            )
        )

    variable_rows = _row_indices_by_label(
        block, row_start + 1, row_end, {"Variable", "Variable Type"}
    )
    type_rows = _row_indices_by_label(block, row_start + 1, row_end, {"Type"})
    default_rows = _row_indices_by_label(block, row_start + 1, row_end, {"Default"})
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
            )
        )

    fields: list[CardFieldIR] = []
    for slot in range(1, slot_count + 1):
        variable = (
            _cell_text(block.rows[variable_row], slot)
            if variable_row is not None
            else None
        )
        field_type = (
            _cell_text(block.rows[type_row], slot) if type_row is not None else None
        )
        default = (
            _cell_text(block.rows[default_row], slot)
            if default_row is not None
            else None
        )
        source_row = row_start
        for candidate_row in (variable_row, type_row, default_row):
            if candidate_row is not None and slot < len(block.rows[candidate_row]):
                source_row = candidate_row
                break
        fields.append(
            CardFieldIR(
                slot=slot,
                variable=variable,
                field_type=field_type,
                default=default,
                source=_cell_source(sourced, source_row, slot),
            )
        )
    return fields


def _is_card_definition_continuation(block: TableBlock) -> bool:
    if not block.rows or _is_variable_description_table(block):
        return False
    labels = {
        row[0].text.strip().casefold()
        for row in block.rows
        if row and row[0].text.strip()
    }
    return bool(labels) and labels <= {
        "variable",
        "variable type",
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
            )
        )
        return
    variable_rows = _row_indices_by_label(
        block, 0, len(block.rows), {"Variable", "Variable Type"}
    )
    type_rows = _row_indices_by_label(block, 0, len(block.rows), {"Type"})
    default_rows = _row_indices_by_label(block, 0, len(block.rows), {"Default"})
    variable_row = variable_rows[0] if variable_rows else None
    type_row = type_rows[0] if type_rows else None
    default_row = default_rows[0] if default_rows else None
    merged: list[CardFieldIR] = []
    for field in card.fields:
        slot = field.slot
        variable = (
            _cell_text(block.rows[variable_row], slot)
            if variable_row is not None
            else None
        )
        field_type = (
            _cell_text(block.rows[type_row], slot) if type_row is not None else None
        )
        default = (
            _cell_text(block.rows[default_row], slot)
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


def _is_variable_description_table(block: TableBlock) -> bool:
    first_row = [value.upper() for value in _table_first_row_text(block)]
    return len(first_row) >= 2 and first_row[:2] == ["VARIABLE", "DESCRIPTION"]


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
    return re.sub(r"\s+", "", value).upper()


def _variable_lookup(keyword: KeywordIR) -> dict[str, str]:
    return {
        _normalized_variable_name(variable): variable
        for variable in keyword.variable_catalog
    }


def _match_variable(lookup: dict[str, str], text: str) -> str | None:
    normalized = _normalized_variable_name(text)
    exact = lookup.get(normalized)
    if exact is not None:
        return exact
    candidates = [
        variable
        for key, variable in lookup.items()
        if len(key) == len(normalized)
        and sum(
            left != right and {left, right} == {"0", "O"}
            for left, right in zip(key, normalized)
        )
        == 1
    ]
    return candidates[0] if len(candidates) == 1 else None


def _match_variable_family(
    lookup: dict[str, str],
    text: str,
) -> list[str] | None:
    """Map a documented variable family label to concrete catalog slots."""

    cleaned = re.sub(r"[^A-Za-z0-9, ]+", "", text).upper().strip()
    if not cleaned:
        return None
    tokens = [token for token in re.split(r"[, ]+", cleaned) if token]
    prefixes: list[str] = []
    for token in tokens:
        if token.endswith("IJ") and token[:-2].isalpha():
            prefix = token[:-2]
        elif token.endswith("I") and token[:-1].isalpha():
            prefix = token[:-1]
        else:
            continue
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    if not prefixes:
        return None
    values = list(lookup.values())
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
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,31}", text.strip()))


def _refresh_variable_catalog(keyword: KeywordIR) -> None:
    seen_variables: set[str] = set()
    keyword.variable_catalog = []
    for card in keyword.cards:
        for field in card.fields:
            if field.variable is None:
                continue
            normalized = field.variable.strip()
            key = normalized.casefold()
            if key not in seen_variables:
                seen_variables.add(key)
                keyword.variable_catalog.append(normalized)


def _get_variable_description(
    keyword: KeywordIR,
    variable: str,
    applies_to: list[str] | None = None,
) -> VariableDescriptionIR:
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
    header_end = 1 if _is_variable_description_table(block) else 0
    active_variable = current_variable
    active_targets: list[str] | None = None
    active_start: int | None = None
    active_continuation_of: BlockSourceRef | None = None
    previous_source: BlockSourceRef | None = None
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

    for row_index in range(header_end, len(block.rows)):
        row = block.rows[row_index]
        label = row[0].text.strip() if row else ""
        if label:
            matched_variable = _match_variable(lookup, label)
            family_variables = (
                None
                if matched_variable is not None
                else _match_variable_family(lookup, label)
            )
            if (
                matched_variable is None
                and family_variables is None
                and active_variable is not None
                and not _looks_like_variable_title(label)
            ):
                # Math/ellipsis cells are often OCR continuation markers.
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
            if active_variable is None:
                keyword.issues.append(
                    _issue(
                        "VARIABLE_DESCRIPTION_UNMATCHED_TITLE",
                        f"variable description title {label!r} is not present in "
                        "the Card variable catalog",
                    )
                )
            continue
        if active_variable is None:
            keyword.issues.append(
                _issue(
                    "VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN",
                    f"variable description continuation row {row_index} has no "
                    "matched variable title",
                )
            )
        elif active_start is None:
            active_start = row_index
            active_continuation_of = previous_source
    flush(len(block.rows))
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
            r"Available\s+options\s+are\s*:?\s*", text, re.IGNORECASE
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
            regions = _card_regions(block)
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
                    row_end=len(block.rows),
                )
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
                    row_end=len(block.rows),
                    continuation_of=previous_source,
                )
                _merge_card_continuation_fields(card, sourced, keyword.issues)
                _refresh_variable_catalog(keyword)
                continue

        if text and (
            _is_exact_label(text, "VARIABLE")
            or _is_exact_label(text, "DESCRIPTION")
        ):
            state = "variables"
            variable_region = True
            pending_card_label = None
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
            matched_variable = _match_variable(lookup, text) if text else None
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
                        )
                    )
            continue
        if state == "cards" and pending_card_label is not None:
            _append_card_condition_block(
                _get_card(keyword, pending_card_label), sourced
            )
            continue
        keyword.unclassified_blocks.append(sourced)

    _refresh_variable_catalog(keyword)


def _title_anchor_indices(section: SectionIR, pdf_page: int) -> list[int]:
    page = next((item for item in section.pages if item.pdf_page == pdf_page), None)
    if page is None:
        return []
    return [
        index
        for index, block in enumerate(page.blocks)
        if isinstance(block, TextBlock)
        and _is_strong_keyword_title(block.text, section.name)
    ]


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
        conventional_pair = False
        if len(owners) == 2:
            left_index, right_index = owners
            left = sections[left_index]
            right = sections[right_index]
            conventional_pair = bool(
                left.document_id == document_id
                and right.document_id == document_id
                and left.source_pages
                and right.source_pages
                and left.source_pages[-1].pdf_page == pdf_page
                and right.source_pages[0].pdf_page == pdf_page
            )
            anchors = _title_anchor_indices(right, pdf_page)
            page = _page_for(right, pdf_page) or _page_for(left, pdf_page)
            if conventional_pair and page is not None and len(anchors) == 1:
                anchor = anchors[0]
                slices[(left_index, pdf_page)] = (0, anchor)
                slices[(right_index, pdf_page)] = (anchor, len(page.blocks))
                message = (
                    f"shared PDF page {pdf_page} was split at block {anchor}, "
                    f"the strong title anchor for {right.name}"
                )
                issues[left_index].append(
                    _issue("KEYWORD_BOUNDARY_RESOLVED", message, severity="info")
                )
                issues[right_index].append(
                    _issue("KEYWORD_BOUNDARY_RESOLVED", message, severity="info")
                )
                continue

        owner_names = ", ".join(sections[index].section_id for index in owners)
        message = (
            f"shared PDF page {pdf_page} could not be split using one strong "
            f"Keyword title anchor; preserving content for {owner_names}"
        )
        for index in owners:
            issues[index].append(_issue("KEYWORD_BOUNDARY_AMBIGUOUS", message))
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

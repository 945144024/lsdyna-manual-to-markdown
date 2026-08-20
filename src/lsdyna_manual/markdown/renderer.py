"""Conservative Markdown rendering for reconstructed KeywordIR objects."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from lsdyna_manual.parser.page_ir import (
    FigureBlock,
    MathBlock,
    TableBlock,
    TextBlock,
    table_grid_rows,
)
from lsdyna_manual.reconstruction.keyword_ir import (
    CardTableIR,
    KeywordIR,
    SourcedBlock,
    VariableDescriptionTableIR,
    card_summary_is_redundant,
    normalize_literal_cell_newlines,
    reconstruct_keywords,
    table_range_signature,
)
from lsdyna_manual.reconstruction.section_ir import SectionIR
from lsdyna_manual.reconstruction.theory_ir import TheoryIR


@dataclass(frozen=True)
class RenderedSection:
    section: KeywordIR | TheoryIR
    markdown_path: Path | None
    manifest_record: dict


def _safe_component(value: str, *, fallback: str = "section") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("._") or fallback


def _family(section: KeywordIR) -> str:
    keyword_id = section.keyword_id
    return _safe_component(keyword_id.split("_", 1)[0], fallback="unknown")


def _relative_path(section: KeywordIR) -> Path:
    volume = section.volume if section.volume is not None else 0
    keyword_id = _safe_component(section.keyword_id, fallback="section")
    return (
        Path("markdown")
        / f"volume-{volume}"
        / _family(section)
        / f"{keyword_id}.md"
    )


def _theory_relative_path(section: TheoryIR) -> Path:
    section_id = _safe_component(section.section_id, fallback="section")
    return Path("markdown") / "theory" / f"{section_id}.md"


def _source_pages(section: KeywordIR) -> list[dict[str, int | str | None]]:
    return [page.to_dict() for page in section.source_pages]


def _front_matter(section: KeywordIR, release: str) -> str:
    metadata = {
        "document_id": section.document_id,
        "manual_type": section.manual_type,
        "keyword_id": section.keyword_id,
        "name": section.name,
        "family": _family(section),
        "legacy_ids": list(section.legacy_ids),
        "options": section.option_names,
        "manual_release": release,
        "volume": section.volume,
        "source_pages": _source_pages(section),
    }
    rendered = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{rendered}\n---"


def _theory_front_matter(section: TheoryIR, release: str) -> str:
    metadata = {
        "document_id": section.document_id,
        "manual_type": section.manual_type,
        "section_id": section.section_id,
        "section_number": section.section_number,
        "title": section.title,
        "parent_section_id": section.parent_section_id,
        "manual_release": release,
        "source_pages": [page.to_dict() for page in section.source_pages],
    }
    rendered = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{rendered}\n---"


def _escape_cell(value: str) -> str:
    normalized = normalize_literal_cell_newlines(value)
    return " <br> ".join(
        line.replace("|", "\\|").strip()
        for line in normalized.splitlines()
        if line.strip()
    )


def _render_table(block: TableBlock) -> list[str]:
    grid_rows = table_grid_rows(block)
    if not grid_rows:
        return ["> [Table content unavailable.]", ""]
    width = max(len(row) for row in grid_rows)
    rows = [
        [_escape_cell(cell.text) for cell in row]
        + [""] * (width - len(row))
        for row in grid_rows
    ]
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    lines.append("")
    return lines


def _render_math(block: MathBlock) -> list[str]:
    text = block.text.strip()
    if not text:
        return ["> [Formula content unavailable.]", ""]
    if text.startswith("$$") and text.endswith("$$"):
        return [text, ""]
    return ["$$", text, "$$", ""]


def _render_blocks(blocks: list[SourcedBlock]) -> list[str]:
    lines: list[str] = []
    for sourced in blocks:
        block = sourced.block
        if isinstance(block, TextBlock):
            text = block.text.strip()
            if text:
                lines.extend([text, ""])
        elif isinstance(block, MathBlock):
            lines.extend(_render_math(block))
        elif isinstance(block, TableBlock):
            lines.extend(_render_table(block))
        elif isinstance(block, FigureBlock):
            source = f"PDF page {sourced.source.pdf_page}"
            if sourced.source.manual_page is not None:
                source += f", manual page {sourced.source.manual_page}"
            lines.extend(
                [
                    f"> [Figure omitted. See source: {source}.]",
                    "",
                ]
            )
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _source_key(sourced: SourcedBlock) -> tuple[str, int, int]:
    source = sourced.source
    return source.document_id, source.pdf_page, source.block_index


def _is_section_header_text(section: KeywordIR, text: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", text).upper()
    full_name = re.sub(r"[^A-Za-z0-9]+", "", section.name).upper()
    root = re.sub(
        r"[^A-Za-z0-9]+", "", section.name.split("_", 1)[0]
    ).upper()
    if not normalized:
        return False
    if normalized in {root, full_name}:
        return True
    title_like = bool(
        re.fullmatch(r"[\s$^*{}_\\A-Za-z0-9.-]+", text)
    )
    return title_like and normalized.startswith(full_name)


def _render_variable_table(
    block: TableBlock,
    row_start: int,
    row_end: int,
) -> list[str]:
    """Render a variable description row range with explicit column labels."""

    return _render_variable_rows(table_grid_rows(block)[row_start:row_end])


def _render_variable_rows(rows: list[list]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized_rows = [
        [_escape_cell(cell.text) for cell in row]
        + [""] * (width - len(row))
        for row in rows
    ]
    lines = [
        "| " + " | ".join(["Variable", "Description"] + [""] * (width - 2)) + " |"
    ]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in normalized_rows)
    lines.append("")
    return lines


def _source_ref_key(source) -> tuple[str, int, int]:
    return source.document_id, source.pdf_page, source.block_index


def _group_table_parts(
    tables: list[CardTableIR] | list[VariableDescriptionTableIR],
) -> list[list[list]]:
    """Join explicitly linked continuation parts without guessing adjacency."""

    groups: list[list[list]] = []
    current_rows: list[list] = []
    previous_source: tuple[str, int, int] | None = None
    for table in tables:
        block = table.source_block.block
        if not isinstance(block, TableBlock):
            continue
        rows = table_grid_rows(block)[table.row_start:table.row_end]
        continuation_key = (
            _source_ref_key(table.continuation_of)
            if table.continuation_of is not None
            else None
        )
        if current_rows and continuation_key == previous_source:
            current_rows.extend(rows)
        else:
            if current_rows:
                groups.append(current_rows)
            current_rows = list(rows)
        previous_source = _source_key(table.source_block)
    if current_rows:
        groups.append(current_rows)
    return groups


def _deduplicate_table_parts(
    tables: list[VariableDescriptionTableIR],
) -> list[VariableDescriptionTableIR]:
    seen: set[tuple[tuple[str, ...], ...]] = set()
    selected: list[VariableDescriptionTableIR] = []
    for table in tables:
        signature = table_range_signature(table)
        if signature and signature in seen:
            continue
        if signature:
            seen.add(signature)
        selected.append(table)
    return selected


def _deduplicate_text_blocks(blocks: list[SourcedBlock]) -> list[SourcedBlock]:
    seen: set[str] = set()
    selected: list[SourcedBlock] = []
    for sourced in blocks:
        if not isinstance(sourced.block, TextBlock):
            selected.append(sourced)
            continue
        signature = re.sub(r"\s+", " ", sourced.block.text).strip()
        if signature and signature in seen:
            continue
        if signature:
            seen.add(signature)
        selected.append(sourced)
    return selected


def _render_semantic_body(section: KeywordIR) -> list[str] | None:
    """Render strong semantic regions and return None when no regions exist."""

    has_semantics = bool(
        section.description_blocks
        or section.purpose_blocks
        or section.options
        or section.cards
        or section.variable_descriptions
        or section.remarks_blocks
        or section.references_blocks
    )
    if not has_semantics:
        return None

    lines: list[str] = []
    consumed: set[tuple[str, int, int]] = set()

    def add_heading(level: int, text: str) -> None:
        lines.extend([f"{'#' * level} {text}", ""])

    def add_blocks(blocks: list[SourcedBlock], *, skip_labels: set[str] | None = None) -> None:
        skip_labels = {label.casefold() for label in (skip_labels or set())}
        selected: list[SourcedBlock] = []
        for sourced in blocks:
            consumed.add(_source_key(sourced))
            if isinstance(sourced.block, TextBlock):
                text = sourced.block.text.strip()
                if (
                    text.casefold() in skip_labels
                    or _is_section_header_text(section, text)
                ):
                    continue
            selected.append(sourced)
        lines.extend(_render_blocks(selected))

    if section.description_blocks:
        add_heading(2, "Description")
        add_blocks(section.description_blocks)

    if section.purpose_blocks:
        add_heading(2, "Purpose")
        add_blocks(section.purpose_blocks)

    if section.option_intro_blocks or section.options:
        add_heading(2, "Options")
        consumed.update(_source_key(block) for block in section.option_intro_blocks)
        for option in section.options:
            lines.extend([f"- {option.name}", ""])
            consumed.update(_source_key(block) for block in option.blocks)
            option_blocks = [
                block
                for block in option.blocks
                if not (
                    isinstance(block.block, TextBlock)
                    and block.block.text.strip().casefold() == option.name.casefold()
                )
            ]
            add_blocks(option_blocks)

    if section.cards:
        add_heading(2, "Card Definitions")
        add_blocks(
            section.card_intro_blocks,
            skip_labels={
                "card summary",
                "card summary:",
                "data card definitions",
                "data card definitions:",
            },
        )
        for card in section.cards:
            add_heading(3, card.label)
            condition_sources = {
                (
                    condition.source.document_id,
                    condition.source.pdf_page,
                    condition.source.block_index,
                )
                for condition in card.conditions
            }
            if card.conditions:
                add_heading(4, "Conditions")
                for condition in card.conditions:
                    source_text = " ".join(condition.source_text.splitlines()).strip()
                    expression = f"{condition.variable} {condition.operator} {' or '.join(condition.values)}"
                    lines.append(f"- `{expression}` — {source_text}")
                    lines.append("")
                    consumed.add(
                        (
                            condition.source.document_id,
                            condition.source.pdf_page,
                            condition.source.block_index,
                        )
                    )
            add_blocks(
                [
                    block
                    for block in card.condition_blocks
                    if _source_key(block) not in condition_sources
                ]
            )
            for table in card.tables:
                consumed.add(_source_key(table.source_block))
            rendered_tables = [
                table
                for table in card.tables
                if not card_summary_is_redundant(card, table)
            ]
            for rows in _group_table_parts(rendered_tables):
                lines.extend(_render_table(TableBlock(rows=rows)))

    if section.variable_descriptions or section.variable_description_blocks:
        add_heading(2, "Variable Descriptions")
        for description in section.variable_descriptions:
            add_heading(3, description.variable)
            if description.applies_to and (
                description.applies_to != [description.variable]
            ):
                lines.extend(
                    [
                        "Applies to: "
                        + ", ".join(f"`{value}`" for value in description.applies_to),
                        "",
                    ]
                )
            consumed.update(_source_key(block) for block in description.blocks)
            description_blocks = [
                block
                for block in description.blocks
                if not (
                    isinstance(block.block, TextBlock)
                    and block.block.text.strip().casefold()
                    == description.variable.casefold()
                )
            ]
            add_blocks(_deduplicate_text_blocks(description_blocks))
            for table in description.tables:
                consumed.add(_source_key(table.source_block))
            for rows in _group_table_parts(
                _deduplicate_table_parts(description.tables)
            ):
                lines.extend(_render_variable_rows(rows))
        for sourced in section.variable_description_blocks:
            key = _source_key(sourced)
            if isinstance(sourced.block, TextBlock):
                text = sourced.block.text.strip().casefold()
                if text in {"variable", "description"} or _is_section_header_text(
                    section, text
                ):
                    consumed.add(key)
            elif isinstance(sourced.block, TableBlock):
                consumed.add(key)
                source_rows = table_grid_rows(sourced.block)
                known_ranges = [
                    (table.row_start, table.row_end)
                    for description in section.variable_descriptions
                    for table in description.tables
                    if _source_key(table.source_block) == key
                ]
                header_end = 0
                if (
                    source_rows
                    and len(source_rows[0]) >= 2
                    and source_rows[0][0].text.strip().casefold() == "variable"
                    and source_rows[0][1].text.strip().casefold()
                    == "description"
                ):
                    header_end = 1
                covered = {
                    row_index
                    for start, end in known_ranges
                    for row_index in range(start, end)
                }
                unknown_start: int | None = None
                for row_index in range(header_end, len(source_rows) + 1):
                    is_unknown = row_index < len(source_rows) and row_index not in covered
                    if is_unknown and unknown_start is None:
                        unknown_start = row_index
                    elif not is_unknown and unknown_start is not None:
                        lines.extend(
                            _render_variable_table(
                                sourced.block,
                                unknown_start,
                                row_index,
                            )
                        )
                        unknown_start = None
                if header_end == 0 and not known_ranges and source_rows:
                    lines.extend(_render_table(sourced.block))

    if section.remarks_blocks:
        add_heading(2, "Remarks")
        add_blocks(section.remarks_blocks, skip_labels={"remarks"})

    if section.references_blocks:
        add_heading(2, "References")
        add_blocks(section.references_blocks, skip_labels={"references"})

    fallback = [
        block
        for block in section.content_blocks()
        if _source_key(block) not in consumed
        and not (
            isinstance(block.block, TextBlock)
            and _is_section_header_text(section, block.block.text.strip())
        )
    ]
    if fallback:
        add_heading(2, "Source Material")
        lines.extend(_render_blocks(fallback))

    while lines and not lines[-1]:
        lines.pop()
    return lines


class MarkdownRenderer:
    """Render KeywordIR while preserving uncertainty as metadata/issues."""

    def render(
        self,
        section: KeywordIR,
        *,
        corpus_root: Path,
        release: str,
    ) -> RenderedSection:
        relative_path = _relative_path(section)
        manifest = {
            "document_id": section.document_id,
            "manual_type": section.manual_type,
            "keyword_id": section.keyword_id,
            "name": section.name,
            "family": _family(section),
            "legacy_ids": list(section.legacy_ids),
            "options": section.option_names,
            "volume": section.volume,
            "source_pages": _source_pages(section),
            "markdown_path": relative_path.as_posix() if section.content_blocks() else None,
            "status": section.status,
        }
        if not section.content_blocks():
            return RenderedSection(section, None, manifest)

        target = corpus_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        body = [_front_matter(section, release), "", f"# {section.name}", ""]
        semantic_body = _render_semantic_body(section)
        body.extend(
            semantic_body
            if semantic_body is not None
            else _render_blocks(section.content_blocks())
        )
        target.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        return RenderedSection(section, target, manifest)


def render_sections(
    sections: list[SectionIR],
    *,
    corpus_root: Path,
    release: str,
) -> list[RenderedSection]:
    """Compatibility entry point that reconstructs KeywordIR first."""

    return render_keywords(
        reconstruct_keywords(sections),
        corpus_root=corpus_root,
        release=release,
    )


def render_keywords(
    keywords: list[KeywordIR],
    *,
    corpus_root: Path,
    release: str,
) -> list[RenderedSection]:
    renderer = MarkdownRenderer()
    return [
        renderer.render(keyword, corpus_root=corpus_root, release=release)
        for keyword in keywords
    ]


def _normalize_title(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    value = value.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _theory_title_block_count(section: TheoryIR) -> int:
    """Count title-anchor blocks represented by the generated H1."""

    if not section.content_blocks:
        return 0
    number = _normalize_title(section.section_number or section.section_id)
    title = _normalize_title(section.title)
    first = section.content_blocks[0].block
    if not isinstance(first, TextBlock):
        return 0
    first_text = _normalize_title(first.text)
    if first_text == f"{number} {title}":
        return 1
    if first_text == number and len(section.content_blocks) > 1:
        second = section.content_blocks[1].block
        if isinstance(second, TextBlock):
            second_text = _normalize_title(second.text)
            if second_text == title:
                return 2
    return 0


class TheoryMarkdownRenderer:
    """Render TheoryIR as a source-preserving chapter Markdown file."""

    def render(
        self,
        section: TheoryIR,
        *,
        corpus_root: Path,
        release: str,
    ) -> RenderedSection:
        relative_path = _theory_relative_path(section)
        manifest = {
            "document_id": section.document_id,
            "manual_type": section.manual_type,
            "section_id": section.section_id,
            "section_number": section.section_number,
            "title": section.title,
            "parent_section_id": section.parent_section_id,
            "source_pages": [page.to_dict() for page in section.source_pages],
            "markdown_path": relative_path.as_posix() if section.content_blocks else None,
            "status": section.status,
        }
        if not section.content_blocks:
            return RenderedSection(section, None, manifest)

        target = corpus_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        heading = " ".join(
            value
            for value in (section.section_number, section.title)
            if value
        )
        body = [_theory_front_matter(section, release), "", f"# {heading}", ""]
        title_blocks = _theory_title_block_count(section)
        body.extend(_render_blocks(section.content_blocks[title_blocks:]))
        target.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        return RenderedSection(section, target, manifest)


def render_theory(
    theories: list[TheoryIR],
    *,
    corpus_root: Path,
    release: str,
) -> list[RenderedSection]:
    renderer = TheoryMarkdownRenderer()
    return [
        renderer.render(theory, corpus_root=corpus_root, release=release)
        for theory in theories
    ]

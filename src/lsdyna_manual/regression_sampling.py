"""Stratified, reproducible semantic regression sampling.

The existing regression matrix validates PageMap / SectionMap.  This module
selects SectionMap chapters for the next stage: provider parsing,
reconstruction, Markdown rendering, and text-layer comparison.  Selection is
copyright-safe: the manifest contains metadata, hashes, and page references,
not extracted Manual text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping

from lsdyna_manual.documents import ManualDocument
from lsdyna_manual.markdown.renderer import render_keywords
from lsdyna_manual.parser.page_ir import PageIR, TableBlock, load_page_ir
from lsdyna_manual.parser.segmentation import Section
from lsdyna_manual.parser.text_extractor import PopplerLayoutExtractor
from lsdyna_manual.reconstruction.keyword_ir import (
    KeywordIR,
    card_summary_is_redundant,
    literal_cell_newline_count,
    reconstruct_keywords,
)
from lsdyna_manual.reconstruction.section_ir import SectionIR, assemble_sections
from lsdyna_manual.reconstruction.theory_ir import TheoryIR, reconstruct_theory
from lsdyna_manual.validation.text_layer import compare_page_text


SCHEMA_VERSION = "0.1"
DEFAULT_TARGETS = {"short": 3, "medium": 4, "long": 3}
DEFAULT_MAX_SECTION_PAGES = 40
LENGTH_BUCKETS = ("short", "medium", "long")

_CARD_RE = re.compile(r"\bCard\s+\d+[A-Za-z]?\b", re.IGNORECASE)
_CONDITION_RE = re.compile(
    r"\b(?:EQ|NE|GE|GT|LE|LT)\.\s*-?(?:\d+(?:\.\d*)?|\.\d+)|"
    r"included\s+if|if\s+and\s+only\s+if",
    re.IGNORECASE,
)
_VARIABLE_FAMILY_RE = re.compile(
    r"\b(?:Aij|Ai\s*,\s*Bi|[A-Z]ij|[A-Z]i)\b"
)
_OPTION_RE = re.compile(
    r"available\s+options?|\bkeyword\s+options?\b|\bOPTION[A-Z0-9_]*\b",
    re.IGNORECASE,
)
_VARIABLE_DESCRIPTION_RE = re.compile(
    r"\bVARIABLE\s+DESCRIPTION\b|\bVARIABLE\b.{0,30}\bDESCRIPTION\b",
    re.IGNORECASE | re.DOTALL,
)
_FIGURE_RE = re.compile(r"\b(?:Figure|Fig\.)\s*\d+", re.IGNORECASE)


def length_bucket(page_count: int) -> str:
    """Return the stable length stratum used by the sampling contract."""

    if page_count <= 2:
        return "short"
    if page_count <= 6:
        return "medium"
    return "long"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _section_key(section: Section) -> tuple[str, str]:
    return section.document_id or "", section.section_id


def load_navigation(intermediate_dir: Path) -> dict[str, list[Section]]:
    """Load all SectionMaps beneath an intermediate artifact directory."""

    navigation: dict[str, list[Section]] = {}
    for sectionmap_path in sorted(Path(intermediate_dir).glob("*/sectionmap.json")):
        payload = json.loads(sectionmap_path.read_text(encoding="utf-8"))
        document_id = payload.get("document", {}).get("document_id")
        if not document_id:
            raise ValueError(f"SectionMap has no document_id: {sectionmap_path}")
        navigation[document_id] = [
            Section(**item) for item in payload.get("sections", [])
        ]
    return navigation


def _page_texts_for_sections(
    sections: Iterable[Section],
    text_pages: list[str],
) -> dict[tuple[str, str], list[str]]:
    result: dict[tuple[str, str], list[str]] = {}
    for section in sections:
        result[_section_key(section)] = [
            text_pages[pdf_page - 1]
            for pdf_page in section.pdf_pages
            if 0 < pdf_page <= len(text_pages)
        ]
    return result


def _feature_flags(
    section: Section,
    pages: list[str],
    *,
    shared_pages: set[int],
) -> tuple[list[str], dict[str, int]]:
    """Infer sampling features from layout text without changing source data."""

    text = "\n".join(pages)
    card_pages = sum(bool(_CARD_RE.search(page)) for page in pages)
    variable_pages = sum(bool(_VARIABLE_DESCRIPTION_RE.search(page)) for page in pages)
    cards = sorted({match.group(0).casefold() for match in _CARD_RE.finditer(text)})
    conditions = len(_CONDITION_RE.findall(text))
    figures = len(_FIGURE_RE.findall(text))
    formulas = text.count("=") + text.count("\\frac") + text.count("∑")
    flags: list[str] = []
    if section.kind != "keyword":
        flags.append("non_keyword")
    if section.parent_section_id is not None:
        flags.append("nested_section")
    if any(page in shared_pages for page in section.pdf_pages):
        flags.append("shared_boundary_candidate")
    if len(cards) >= 2:
        flags.append("multiple_cards_candidate")
    if conditions:
        flags.append("card_conditions_candidate")
    if _OPTION_RE.search(text):
        flags.append("options_candidate")
    if _VARIABLE_DESCRIPTION_RE.search(text):
        flags.append("variable_description_candidate")
    if _VARIABLE_FAMILY_RE.search(text):
        flags.append("variable_family_candidate")
    if card_pages >= 2:
        flags.append("multi_page_card_candidate")
    if variable_pages >= 2:
        flags.append("multi_page_variable_description_candidate")
    if formulas >= 6:
        flags.append("formula_dense_candidate")
    if figures >= 2:
        flags.append("figure_rich_candidate")
    return flags, {
        "card_label_count": len(cards),
        "condition_match_count": conditions,
        "card_page_count": card_pages,
        "variable_description_page_count": variable_pages,
        "formula_signal_count": formulas,
        "figure_reference_count": figures,
    }


def _pick(
    candidates: list[Section],
    count: int,
    *,
    seed: int,
    namespace: str,
) -> list[Section]:
    ordered = sorted(candidates, key=lambda item: (_section_key(item), item.pdf_pages))
    if count >= len(ordered):
        return ordered
    rng = random.Random(f"{seed}:{namespace}")
    chosen = rng.sample(ordered, count)
    return sorted(chosen, key=lambda item: (_section_key(item), item.pdf_pages))


def build_sample_manifest(
    *,
    release: str,
    documents: Mapping[str, ManualDocument],
    navigation: Mapping[str, list[Section]],
    source_text: Mapping[str, list[str]] | None = None,
    seed: int = 20260817,
    targets: Mapping[str, int] = DEFAULT_TARGETS,
    max_section_pages: int = DEFAULT_MAX_SECTION_PAGES,
    anchor_sections: Iterable[tuple[str, str]] = (),
) -> dict:
    """Build a reproducible stratified sample plus rare-feature supplements."""

    source_text = source_text or {}
    anchor_sections = tuple(anchor_sections)
    shared_counts: Counter[tuple[str, int]] = Counter(
        (section.document_id or "", page)
        for sections in navigation.values()
        for section in sections
        for page in section.pdf_pages
    )
    feature_data: dict[tuple[str, str], tuple[list[str], dict[str, int]]] = {}
    selected: dict[tuple[str, str], dict] = {}
    strata_summary: dict[str, dict[str, int]] = {}

    for document_id in sorted(navigation):
        sections = navigation[document_id]
        text_pages = source_text.get(document_id, [])
        text_by_section = _page_texts_for_sections(sections, text_pages)
        buckets: dict[str, list[Section]] = {bucket: [] for bucket in LENGTH_BUCKETS}
        for section in sections:
            page_count = len(section.pdf_pages)
            if page_count <= 0 or page_count > max_section_pages:
                continue
            bucket = length_bucket(page_count)
            section_text_pages = text_by_section.get(_section_key(section), [])
            flags, signals = _feature_flags(
                section,
                section_text_pages,
                shared_pages={
                    page
                    for page in section.pdf_pages
                    if shared_counts[(document_id, page)] > 1
                },
            )
            feature_data[_section_key(section)] = (flags, signals)
            buckets[bucket].append(section)
        strata_summary[document_id] = {
            bucket: len(buckets[bucket]) for bucket in LENGTH_BUCKETS
        }
        for bucket in LENGTH_BUCKETS:
            for section in _pick(
                buckets[bucket],
                targets.get(bucket, 0),
                seed=seed,
                namespace=f"{document_id}:{bucket}",
            ):
                key = _section_key(section)
                flags, signals = feature_data[key]
                selected[key] = {
                    "document_id": document_id,
                    "section_id": section.section_id,
                    "keyword_id": section.keyword_id,
                    "name": section.name,
                    "kind": section.kind,
                    "volume": section.volume,
                    "parent_section_id": section.parent_section_id,
                    "section_number": section.section_number,
                    "manual_type": (
                        "keyword"
                        if section.kind == "keyword"
                        else "theory"
                        if section.kind == "theory"
                        else "document"
                    ),
                    "length_bucket": bucket,
                    "page_count": len(section.pdf_pages),
                    "pdf_pages": list(section.pdf_pages),
                    "manual_pages": list(section.manual_pages),
                    "feature_flags": flags,
                    "feature_signals": signals,
                    "selection_reasons": [f"length_stratum:{bucket}"],
                }

    section_index = {
        _section_key(section): section
        for sections in navigation.values()
        for section in sections
    }
    missing_anchors: list[str] = []
    for key in anchor_sections:
        section = section_index.get(key)
        if section is None:
            missing_anchors.append(":".join(key))
            continue
        if key in selected:
            selected[key]["selection_reasons"].append("anchor:explicit")
            continue
        flags, signals = feature_data.get(key, ([], {}))
        selected[key] = {
            "document_id": section.document_id,
            "section_id": section.section_id,
            "keyword_id": section.keyword_id,
            "name": section.name,
            "kind": section.kind,
            "volume": section.volume,
            "parent_section_id": section.parent_section_id,
            "section_number": section.section_number,
            "manual_type": (
                "keyword"
                if section.kind == "keyword"
                else "theory"
                if section.kind == "theory"
                else "document"
            ),
            "length_bucket": length_bucket(len(section.pdf_pages)),
            "page_count": len(section.pdf_pages),
            "pdf_pages": list(section.pdf_pages),
            "manual_pages": list(section.manual_pages),
            "feature_flags": flags,
            "feature_signals": signals,
            "selection_reasons": ["anchor:explicit"],
        }

    rare_features = (
        "card_conditions_candidate",
        "multi_page_card_candidate",
        "multi_page_variable_description_candidate",
        "variable_family_candidate",
        "formula_dense_candidate",
        "figure_rich_candidate",
        "multiple_cards_candidate",
        "options_candidate",
        "non_keyword",
    )
    all_candidates = {
        key: section
        for document_id, sections in navigation.items()
        for section in sections
        if len(section.pdf_pages) <= max_section_pages
        for key in [_section_key(section)]
    }
    uncovered_features: list[str] = []
    for feature in rare_features:
        current = [item for item in selected.values() if feature in item["feature_flags"]]
        if current:
            for item in current:
                item["selection_reasons"].append(f"feature:{feature}")
            continue
        candidates = [
            section
            for key, section in all_candidates.items()
            if key not in selected
            and feature in feature_data.get(key, ([], {}))[0]
        ]
        picked = _pick(
            candidates,
            1,
            seed=seed,
            namespace=f"rare:{feature}",
        )
        if not picked:
            uncovered_features.append(feature)
            continue
        section = picked[0]
        key = _section_key(section)
        flags, signals = feature_data[key]
        item = {
            "document_id": section.document_id,
            "section_id": section.section_id,
            "keyword_id": section.keyword_id,
            "name": section.name,
            "kind": section.kind,
            "volume": section.volume,
            "parent_section_id": section.parent_section_id,
            "section_number": section.section_number,
            "manual_type": (
                "keyword"
                if section.kind == "keyword"
                else "theory"
                if section.kind == "theory"
                else "document"
            ),
            "length_bucket": length_bucket(len(section.pdf_pages)),
            "page_count": len(section.pdf_pages),
            "pdf_pages": list(section.pdf_pages),
            "manual_pages": list(section.manual_pages),
            "feature_flags": flags,
            "feature_signals": signals,
            "selection_reasons": [f"feature:{feature}"],
        }
        selected[key] = item

    records = sorted(
        selected.values(),
        key=lambda item: (
            item["document_id"],
            item["pdf_pages"][0] if item["pdf_pages"] else 0,
            item["section_id"],
        ),
    )
    for index, item in enumerate(records, start=1):
        item["sample_id"] = f"{release}-{index:03d}"

    source_records = []
    for document_id in sorted(documents):
        document = documents[document_id]
        source_records.append(
            {
                "document_id": document_id,
                "manual_type": document.manual_type,
                "volume": document.volume,
                "source_file": document.path.name,
                "source_sha256": _sha256(document.path),
                "section_count": len(navigation.get(document_id, [])),
                "eligible_section_count": strata_summary.get(document_id, {}),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "semantic-regression-sample",
        "release": release,
        "seed": seed,
        "selection": {
            "length_targets": dict(targets),
            "max_section_pages": max_section_pages,
            "rare_features": list(rare_features),
            "uncovered_features": uncovered_features,
            "anchors": [":".join(key) for key in anchor_sections],
            "missing_anchors": missing_anchors,
        },
        "documents": source_records,
        "samples": records,
        "summary": {
            "sample_count": len(records),
            "sample_pages": sum(item["page_count"] for item in records),
            "by_document": dict(Counter(item["document_id"] for item in records)),
            "by_length_bucket": dict(Counter(item["length_bucket"] for item in records)),
            "by_feature": {
                feature: sum(feature in item["feature_flags"] for item in records)
                for feature in rare_features
            },
        },
    }


def _load_pageirs(pageir_root: Path, document_id: str) -> dict[int, PageIR]:
    pages: dict[int, PageIR] = {}
    for path in sorted((pageir_root / document_id).glob("page_*.json")):
        page = load_page_ir(path)
        pages[page.pdf_page] = page
    return pages


def _issue_counts(issues: Iterable) -> dict[str, int]:
    return dict(Counter(issue.code for issue in issues))


def _confusable_pairs(values: Iterable[str]) -> list[list[str]]:
    groups: dict[str, set[str]] = {}
    for value in values:
        normalized = value.casefold().replace("o", "0")
        groups.setdefault(normalized, set()).add(value)
    return [sorted(group) for group in groups.values() if len(group) > 1]


def keyword_quality_findings(keyword: KeywordIR, markdown: str) -> dict:
    """Return concrete, reviewable Markdown quality candidates."""

    dual_render_cards = [
        card.label
        for card in keyword.cards
        if {table.role for table in card.tables} >= {"summary", "definition"}
        and any(
            table.role == "summary"
            and not card_summary_is_redundant(card, table)
            for table in card.tables
        )
    ]
    values = list(keyword.variable_catalog)
    values.extend(description.variable for description in keyword.variable_descriptions)
    for description in keyword.variable_descriptions:
        for table in description.tables:
            block = table.source_block.block
            if isinstance(block, TableBlock):
                values.extend(
                    cell.text.strip()
                    for row in block.rows[table.row_start:table.row_end]
                    for cell in row
                    if cell.text.strip()
                )
    for card in keyword.cards:
        values.extend(field.variable or "" for field in card.fields)
        for table in card.tables:
            block = table.source_block.block
            if isinstance(block, TableBlock):
                values.extend(
                    cell.text.strip()
                    for row in block.rows[table.row_start:table.row_end]
                    for cell in row
                    if cell.text.strip()
                )
    return {
        "card_summary_definition_dual_render": dual_render_cards,
        # The renderer removes exact duplicate fragments. Multiple distinct
        # root tables for one variable are valid for option-dependent Cards.
        "duplicate_variable_descriptions": [],
        "confusable_identifier_pairs": _confusable_pairs(
            value for value in values if value
        ),
        "literal_backslash_n_count": literal_cell_newline_count(markdown),
        "source_material_fallback": "## Source Material" in markdown,
    }


def _quality_flags(findings: Mapping) -> list[str]:
    mapping = {
        "card_summary_definition_dual_render": (
            "card_summary_definition_dual_render_candidate"
        ),
        "duplicate_variable_descriptions": (
            "duplicate_variable_description_candidate"
        ),
        "confusable_identifier_pairs": "confusable_identifier_candidate",
        "literal_backslash_n_count": "literal_backslash_n_candidate",
        "source_material_fallback": "source_material_fallback_candidate",
    }
    return [flag for key, flag in mapping.items() if findings.get(key)]


def detect_sample_manifest(
    *,
    manifest: Mapping,
    documents: Mapping[str, ManualDocument],
    navigation: Mapping[str, list[Section]],
    pageir_root: Path,
    output_dir: Path,
    text_layer_enabled: bool = True,
    text_layer_min_tokens: int = 8,
    text_layer_min_visual_recall: float = 0.65,
) -> dict:
    """Detect current sample coverage and semantic quality without OCR calls."""

    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    keyword_by_document: dict[str, dict[str, KeywordIR]] = {}
    theory_by_document: dict[str, dict[str, TheoryIR]] = {}
    section_by_document: dict[str, dict[str, SectionIR]] = {}
    pageirs_by_document: dict[str, dict[int, PageIR]] = {}
    text_pages_by_document: dict[str, list[str]] = {}
    text_layer_issues: list[dict] = []

    samples_by_key = {
        (sample["document_id"], sample["section_id"]): sample
        for sample in manifest.get("samples", [])
    }
    for document_id, sections in navigation.items():
        pages = _load_pageirs(pageir_root, document_id)
        pageirs_by_document[document_id] = pages
        if pages:
            effective_sections = []
            for section in sections:
                sample = samples_by_key.get((document_id, section.section_id))
                if sample is None:
                    effective_sections.append(section)
                    continue
                manual_by_pdf = dict(
                    zip(section.pdf_pages, section.manual_pages, strict=False)
                )
                sample_pages = list(sample["pdf_pages"])
                effective_sections.append(
                    replace(
                        section,
                        pdf_pages=sample_pages,
                        manual_pages=(
                            list(sample["manual_pages"])
                            if "manual_pages" in sample
                            else [manual_by_pdf.get(page) for page in sample_pages]
                        ),
                    )
                )
            assembled = assemble_sections(
                effective_sections,
                {(document_id, pdf_page): page for pdf_page, page in pages.items()},
            )
            section_by_document[document_id] = {
                section.section_id: section for section in assembled
            }
            keyword_by_document[document_id] = {
                keyword.section_id: keyword
                for keyword in reconstruct_keywords(assembled)
            }
            theory_by_document[document_id] = {
                theory.section_id: theory
                for theory in reconstruct_theory(assembled)
            }
        if text_layer_enabled and document_id in documents:
            try:
                text_pages_by_document[document_id] = PopplerLayoutExtractor().extract_pages(
                    documents[document_id].path
                )
            except (OSError, RuntimeError):
                text_pages_by_document[document_id] = []

    samples_by_document: dict[str, list[dict]] = {}
    for sample in manifest.get("samples", []):
        samples_by_document.setdefault(sample["document_id"], []).append(sample)

    selected_keywords: list[KeywordIR] = []
    for sample in manifest.get("samples", []):
        document_id = sample["document_id"]
        pages = pageirs_by_document.get(document_id, {})
        expected_pages = sample["pdf_pages"]
        found_pages = [page for page in expected_pages if page in pages]
        coverage = len(found_pages) / len(expected_pages) if expected_pages else 0.0
        record = {
            "sample_id": sample["sample_id"],
            "document_id": document_id,
            "section_id": sample["section_id"],
            "name": sample["name"],
            "kind": sample["kind"],
            "length_bucket": sample["length_bucket"],
            "page_count": sample["page_count"],
            "expected_pdf_pages": expected_pages,
            "available_pdf_pages": found_pages,
            "pageir_coverage": coverage,
            "selection_reasons": sample["selection_reasons"],
            "feature_flags": sample["feature_flags"],
            "status": "not_parsed" if not found_pages else "partial" if coverage < 1 else "checked",
            "issues": {},
            "issue_details": [],
        }
        keyword = keyword_by_document.get(document_id, {}).get(sample["section_id"])
        theory = theory_by_document.get(document_id, {}).get(sample["section_id"])
        if coverage == 1 and keyword is not None:
            selected_keywords.append(keyword)
            record["status"] = "warning" if keyword.status == "warning" else "checked"
            record["issues"] = _issue_counts(keyword.issues)
            record["issue_details"] = [issue.to_dict() for issue in keyword.issues]
            card_conditions = sum(len(card.conditions) for card in keyword.cards)
            continuation_count = sum(
                table.continuation_of is not None
                for card in keyword.cards
                for table in card.tables
            ) + sum(
                table.continuation_of is not None
                for description in keyword.variable_descriptions
                for table in description.tables
            )
            record["semantic"] = {
                "card_count": len(keyword.cards),
                "card_field_count": sum(len(card.fields) for card in keyword.cards),
                "card_condition_count": card_conditions,
                "variable_description_count": len(keyword.variable_descriptions),
                "variable_family_count": sum(
                    description.applies_to != [description.variable]
                    for description in keyword.variable_descriptions
                ),
                "continuation_table_count": continuation_count,
                "unclassified_block_count": len(keyword.unclassified_blocks),
                "block_accounting_ok": not any(
                    issue.code.endswith("ACCOUNTING_MISMATCH")
                    for issue in keyword.issues
                ),
            }
        elif coverage == 1 and theory is not None:
            record["status"] = "warning" if theory.status == "warning" else "checked"
            record["issues"] = _issue_counts(theory.issues)
            record["issue_details"] = [issue.to_dict() for issue in theory.issues]
            record["semantic"] = {
                "theory_owned_block_count": len(theory.owned_sources),
                "theory_content_block_count": len(theory.content_blocks),
                "theory_ignored_block_count": len(theory.ignored_blocks),
                "block_accounting_ok": len(theory.owned_sources)
                == len(theory.content_blocks) + len(theory.ignored_blocks),
            }
        elif coverage == 1:
            section = section_by_document.get(document_id, {}).get(
                sample["section_id"]
            )
            if section is not None:
                record["issues"] = _issue_counts(section.issues)
                record["issue_details"] = [
                    issue.to_dict() for issue in section.issues
                ]
                record["status"] = (
                    "warning" if section.status == "warning" else "checked"
                )
            record["semantic"] = {"pageir_only": True}
        if coverage == 1 and text_layer_enabled:
            text_pages = text_pages_by_document.get(document_id, [])
            section_pages = [pages[page] for page in expected_pages]
            if text_pages and section_pages:
                sample_page_numbers = [
                    expected_pages[0],
                    expected_pages[len(expected_pages) // 2],
                    expected_pages[-1],
                ]
                seen_pages: set[int] = set()
                text_layer_samples = []
                for pdf_page in sample_page_numbers:
                    if pdf_page in seen_pages or pdf_page not in pages:
                        continue
                    seen_pages.add(pdf_page)
                    if 0 < pdf_page <= len(text_pages):
                        comparison = compare_page_text(
                            document_id=document_id,
                            page_ir=pages[pdf_page],
                            text_layer=text_pages[pdf_page - 1],
                            min_tokens=text_layer_min_tokens,
                            min_visual_recall=text_layer_min_visual_recall,
                        )
                        text_layer_samples.append(comparison.to_dict())
                        text_layer_issues.extend(
                            {
                                "sample_id": sample["sample_id"],
                                "pdf_page": pdf_page,
                                **issue.to_dict(),
                            }
                            for issue in comparison.issues
                        )
                record["text_layer"] = {"samples": text_layer_samples}
        records.append(record)

    if selected_keywords:
        rendered = render_keywords(
            selected_keywords,
            corpus_root=output_dir,
            release=str(manifest.get("release", "")),
        )
        rendered_by_section = {
            (item.section.document_id, item.section.section_id): item
            for item in rendered
        }
        for record in records:
            item = rendered_by_section.get(
                (record["document_id"], record["section_id"])
            )
            if item is None:
                continue
            record["markdown_path"] = (
                str(item.markdown_path.relative_to(output_dir))
                if item.markdown_path is not None
                else None
            )
            markdown = item.markdown_path.read_text(encoding="utf-8") if item.markdown_path else ""
            keyword = keyword_by_document[record["document_id"]].get(record["section_id"])
            if keyword is not None:
                findings = keyword_quality_findings(keyword, markdown)
                record["quality_findings"] = findings
                record["quality_flags"] = _quality_flags(findings)
                record["markdown"] = {
                    "has_conditions_heading": "#### Conditions" in markdown,
                    "has_applies_to": "Applies to:" in markdown,
                    "source_material_fallback": "## Source Material" in markdown,
                    "literal_backslash_n_count": markdown.count("\\n"),
                }

    issue_counts = Counter()
    for record in records:
        issue_counts.update(record.get("issues", {}))
        issue_counts.update(record.get("quality_flags", []))
    issue_counts.update(issue["code"] for issue in text_layer_issues)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "semantic-regression-detection",
        "release": manifest.get("release"),
        "seed": manifest.get("seed"),
        "sample_manifest": "sample_manifest.json",
        "samples": records,
        "text_layer_issues": text_layer_issues,
        "summary": {
            "sample_count": len(records),
            "not_parsed_count": sum(record["status"] == "not_parsed" for record in records),
            "partial_count": sum(record["status"] == "partial" for record in records),
            "checked_count": sum(record["status"] in {"checked", "warning"} for record in records),
            "warning_count": sum(record["status"] == "warning" for record in records),
            "pageir_expected_pages": sum(len(record["expected_pdf_pages"]) for record in records),
            "pageir_available_pages": sum(len(record["available_pdf_pages"]) for record in records),
            "issue_counts": dict(issue_counts),
        },
    }


def write_sampling_outputs(
    manifest: Mapping,
    report: Mapping,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "sample_manifest.json"
    report_path = output_dir / "sample_detection.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path, report_path


def load_sample_page_keys(
    path: Path,
    *,
    release: str,
    documents: Mapping[str, ManualDocument],
) -> set[tuple[str, int]]:
    """Load and validate the page set selected by a sample manifest."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported sample manifest schema: {path}")
    if payload.get("kind") != "semantic-regression-sample":
        raise ValueError(f"not a semantic regression sample manifest: {path}")
    if payload.get("release") != release:
        raise ValueError(
            f"sample manifest release {payload.get('release')!r} does not match {release!r}"
        )
    for source in payload.get("documents", []):
        document_id = source.get("document_id")
        document = documents.get(document_id)
        if document is None:
            raise ValueError(f"sample manifest document is not configured: {document_id}")
        expected = source.get("source_sha256")
        if expected and _sha256(document.path) != expected:
            raise ValueError(
                f"source PDF hash changed for sample document {document_id}"
            )
    return {
        (sample["document_id"], pdf_page)
        for sample in payload.get("samples", [])
        for pdf_page in sample.get("pdf_pages", [])
    }


def _discover_documents(manuals_dir: Path, release: str) -> dict[str, ManualDocument]:
    from lsdyna_manual.parser.discovery import discover_documents

    return {
        document.document_id: document
        for document in discover_documents(manuals_dir, expected_release=release)
    }


def run_sampling(
    *,
    manuals_dir: Path,
    release: str,
    intermediate_dir: Path,
    pageir_root: Path,
    output_dir: Path,
    seed: int = 20260817,
    anchor_sections: Iterable[tuple[str, str]] = (),
) -> tuple[dict, dict]:
    """Generate and detect one semantic sample set for a release."""

    documents = _discover_documents(Path(manuals_dir), release)
    navigation = load_navigation(Path(intermediate_dir))
    missing = sorted(set(documents) - set(navigation))
    if missing:
        raise ValueError(
            "SectionMap artifacts missing for configured documents: "
            + ", ".join(missing)
        )
    source_text: dict[str, list[str]] = {}
    extractor = PopplerLayoutExtractor()
    for document_id, document in documents.items():
        source_text[document_id] = extractor.extract_pages(document.path)
    manifest = build_sample_manifest(
        release=release,
        documents=documents,
        navigation=navigation,
        source_text=source_text,
        seed=seed,
        anchor_sections=anchor_sections,
    )
    report = detect_sample_manifest(
        manifest=manifest,
        documents=documents,
        navigation=navigation,
        pageir_root=Path(pageir_root),
        output_dir=Path(output_dir),
    )
    write_sampling_outputs(manifest, report, Path(output_dir))
    return manifest, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuals-dir", type=Path, default=Path("manuals"))
    parser.add_argument("--release", default="R17")
    parser.add_argument(
        "--intermediate-dir",
        type=Path,
        default=Path("workspace/regression/r17/intermediate"),
    )
    parser.add_argument(
        "--pageir-dir",
        type=Path,
        default=Path("workspace/run_r17/parsing/pageir"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("workspace/regression/r17/semantic-sample"),
    )
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument(
        "--anchor",
        action="append",
        default=[],
        metavar="DOCUMENT_ID:SECTION_ID",
        help="append a known edge-case section without changing random strata",
    )
    args = parser.parse_args(argv)
    anchors: list[tuple[str, str]] = []
    for value in args.anchor:
        if ":" not in value:
            parser.error(f"invalid --anchor {value!r}; expected DOCUMENT_ID:SECTION_ID")
        anchors.append(tuple(value.split(":", 1)))
    manifest, report = run_sampling(
        manuals_dir=args.manuals_dir,
        release=args.release,
        intermediate_dir=args.intermediate_dir,
        pageir_root=args.pageir_dir,
        output_dir=args.output_dir,
        seed=args.seed,
        anchor_sections=anchors,
    )
    print(
        f"samples={manifest['summary']['sample_count']} "
        f"pages={manifest['summary']['sample_pages']} "
        f"checked={report['summary']['checked_count']} "
        f"partial={report['summary']['partial_count']} "
        f"not_parsed={report['summary']['not_parsed_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

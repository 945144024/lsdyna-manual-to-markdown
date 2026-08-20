"""Tests for reproducible semantic regression sampling."""

import json
from pathlib import Path

from lsdyna_manual.documents import ManualDocument
from lsdyna_manual.parser.page_ir import (
    Cell,
    PageIR,
    ParseIssue,
    TableBlock,
    TextBlock,
    save_page_ir,
)
from lsdyna_manual.parser.segmentation import Section
from lsdyna_manual.reconstruction.keyword_ir import (
    BlockSourceRef,
    CardIR,
    CardTableIR,
    KeywordIR,
    SourcedBlock,
    VariableDescriptionIR,
    VariableDescriptionTableIR,
)
from lsdyna_manual.regression_sampling import (
    build_sample_manifest,
    detect_sample_manifest,
    keyword_quality_findings,
    length_bucket,
    load_sample_page_keys,
    run_manifest_detection,
)


def _document(tmp_path: Path, document_id: str, volume: int | None) -> ManualDocument:
    path = tmp_path / f"{document_id}.pdf"
    path.write_bytes(document_id.encode("ascii"))
    return ManualDocument(
        document_id=document_id,
        manual_type="theory" if volume is None else "keyword",
        release="R17",
        path=path,
        volume=volume,
    )


def _sections(document_id: str, volume: int | None) -> list[Section]:
    sections = []
    page = 1
    for index, page_count in enumerate(
        (1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11), start=1
    ):
        pages = list(range(page, page + page_count))
        sections.append(
            Section(
                section_id=f"{document_id}-{index}",
                keyword_id=None if volume is None else f"K_{index}",
                name=("Theory section" if volume is None else f"*K_{index}"),
                volume=volume,
                kind="theory" if volume is None else "keyword",
                parent_section_id=None,
                pdf_pages=pages,
                manual_pages=[f"{index}-{n}" for n in range(page_count)],
                document_id=document_id,
                section_number=str(index),
            )
        )
        page += page_count
    return sections


def test_length_bucket_boundaries():
    assert [length_bucket(value) for value in (1, 2, 3, 6, 7)] == [
        "short",
        "short",
        "medium",
        "medium",
        "long",
    ]


def test_sampling_is_reproducible_and_stratified(tmp_path):
    documents = {
        document_id: _document(tmp_path, document_id, volume)
        for document_id, volume in (
            ("keyword-volume-1", 1),
            ("keyword-volume-2", 2),
            ("keyword-volume-3", 3),
            ("theory", None),
        )
    }
    navigation = {
        document_id: _sections(document_id, document.volume)
        for document_id, document in documents.items()
    }
    source_text = {
        document_id: [
            "Card 1 Card 2 VARIABLE DESCRIPTION EQ. 1 Aij Figure 1 = x"
        ]
        * 80
        for document_id in documents
    }

    first = build_sample_manifest(
        release="R17",
        documents=documents,
        navigation=navigation,
        source_text=source_text,
        seed=1234,
    )
    second = build_sample_manifest(
        release="R17",
        documents=documents,
        navigation=navigation,
        source_text=source_text,
        seed=1234,
    )

    assert first == second
    assert first["summary"]["by_document"] == {
        "keyword-volume-1": 10,
        "keyword-volume-2": 10,
        "keyword-volume-3": 10,
        "theory": 10,
    }
    for document_id in documents:
        selected = [
            item for item in first["samples"] if item["document_id"] == document_id
        ]
        assert {item["length_bucket"] for item in selected} == {
            "short",
            "medium",
            "long",
        }
        assert all(item["selection_reasons"] for item in selected)
    assert all(record["source_sha256"] for record in first["documents"])


def test_sampling_records_feature_supplement_reason(tmp_path):
    document = _document(tmp_path, "keyword-volume-2", 2)
    sections = _sections(document.document_id, document.volume)
    source_text = ["plain"] * 80
    special = sections[-1]
    for page in special.pdf_pages:
        source_text[page - 1] = "Card 1 Card 2 VARIABLE DESCRIPTION EQ. 1 Aij"

    manifest = build_sample_manifest(
        release="R17",
        documents={document.document_id: document},
        navigation={document.document_id: sections},
        source_text={document.document_id: source_text},
        seed=2,
        targets={"short": 0, "medium": 0, "long": 0},
    )

    assert manifest["summary"]["sample_count"] >= 1
    assert any(
        reason.startswith("feature:")
        for item in manifest["samples"]
        for reason in item["selection_reasons"]
    )


def test_sampling_keeps_explicit_anchor_outside_random_strata(tmp_path):
    document = _document(tmp_path, "keyword-volume-2", 2)
    sections = _sections(document.document_id, document.volume)
    manifest = build_sample_manifest(
        release="R17",
        documents={document.document_id: document},
        navigation={document.document_id: sections},
        source_text={document.document_id: ["plain"] * 80},
        seed=5,
        anchor_sections=[(document.document_id, sections[0].section_id)],
    )

    anchor = next(
        item
        for item in manifest["samples"]
        if item["section_id"] == sections[0].section_id
    )
    assert "anchor:explicit" in anchor["selection_reasons"]
    assert ":".join((document.document_id, sections[0].section_id)) in manifest[
        "selection"
    ]["anchors"]


def test_keyword_quality_findings_name_concrete_candidates():
    source = BlockSourceRef("keyword-volume-2", 1, 0)
    table = SourcedBlock(
        source=source,
        block=TableBlock(
            rows=[[
                Cell(text="EO", row=0, column=0),
                Cell(text="E0", row=0, column=1),
                Cell(text="VO", row=0, column=2),
                Cell(text="V0", row=0, column=3),
            ]]
        ),
    )
    keyword = KeywordIR(
        document_id="keyword-volume-2",
        section_id="EOS_EXAMPLE",
        keyword_id="EOS_EXAMPLE",
        name="*EOS_EXAMPLE",
        volume=2,
        cards=[
            CardIR(
                label="Card 1",
                tables=[
                    CardTableIR(table, "summary", 0, 1),
                    CardTableIR(table, "definition", 0, 1),
                ],
            )
        ],
        variable_descriptions=[
            VariableDescriptionIR(
                variable="E0",
                tables=[
                    VariableDescriptionTableIR(table, 0, 1),
                    VariableDescriptionTableIR(table, 0, 1),
                ],
            )
        ],
    )

    findings = keyword_quality_findings(
        keyword,
        "value\\nnext\n\n## Source Material\n",
    )

    assert findings["card_summary_definition_dual_render"] == ["Card 1"]
    assert findings["duplicate_variable_descriptions"] == []
    assert ["E0", "EO"] in findings["confusable_identifier_pairs"]
    assert ["V0", "VO"] in findings["confusable_identifier_pairs"]
    assert findings["literal_backslash_n_count"] == 1
    assert findings["source_material_fallback"] is True


def test_sample_manifest_page_keys_validate_source_hash(tmp_path):
    document = _document(tmp_path, "keyword-volume-2", 2)
    manifest = build_sample_manifest(
        release="R17",
        documents={document.document_id: document},
        navigation={document.document_id: _sections(document.document_id, 2)},
        seed=7,
        targets={"short": 1, "medium": 0, "long": 0},
    )
    path = tmp_path / "sample_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    keys = load_sample_page_keys(
        path,
        release="R17",
        documents={document.document_id: document},
    )

    assert keys


def test_detection_reports_theory_pageir_issues(tmp_path):
    document = _document(tmp_path, "theory", None)
    section = _sections(document.document_id, None)[0]
    pageir_root = tmp_path / "pageir"
    save_page_ir(
        PageIR(
            document_id="theory",
            pdf_page=1,
            manual_page="1-1",
            blocks=[TextBlock(text="1 Theory section")],
            issues=[
                ParseIssue(
                    severity="warning",
                    code="READING_ORDER_AMBIGUOUS",
                    message="test",
                )
            ],
        ),
        pageir_root / "theory" / "page_000001.json",
    )
    manifest = {
        "schema_version": "0.1",
        "kind": "semantic-regression-sample",
        "release": "R17",
        "seed": 1,
        "samples": [
            {
                "sample_id": "R17-THEORY-001",
                "document_id": "theory",
                "section_id": section.section_id,
                "name": section.name,
                "kind": "theory",
                "length_bucket": "short",
                "page_count": 1,
                "pdf_pages": [1],
                "selection_reasons": [],
                "feature_flags": [],
            }
        ],
    }

    report = detect_sample_manifest(
        manifest=manifest,
        documents={"theory": document},
        navigation={"theory": [section]},
        pageir_root=pageir_root,
        output_dir=tmp_path / "report",
        text_layer_enabled=False,
    )

    record = report["samples"][0]
    assert record["status"] == "warning"
    assert record["issues"] == {"READING_ORDER_AMBIGUOUS": 1}
    assert record["issue_details"] == [
        {
            "severity": "warning",
            "code": "READING_ORDER_AMBIGUOUS",
            "message": "test",
            "pdf_page": 1,
            "manual_page": "1-1",
        }
    ]


def test_detection_reports_keyword_issue_details(tmp_path):
    document = _document(tmp_path, "keyword-volume-2", 2)
    section = _sections(document.document_id, 2)[0]
    pageir_root = tmp_path / "pageir"
    save_page_ir(
        PageIR(
            document_id=document.document_id,
            pdf_page=1,
            manual_page="1-1",
            blocks=[TextBlock(text="*K_1\nDescription")],
            issues=[
                ParseIssue(
                    severity="warning",
                    code="READING_ORDER_AMBIGUOUS",
                    message="keyword test",
                )
            ],
        ),
        pageir_root / document.document_id / "page_000001.json",
    )
    manifest = {
        "schema_version": "0.1",
        "kind": "semantic-regression-sample",
        "release": "R17",
        "seed": 1,
        "samples": [
            {
                "sample_id": "R17-KEYWORD-001",
                "document_id": document.document_id,
                "section_id": section.section_id,
                "name": section.name,
                "kind": "keyword",
                "length_bucket": "short",
                "page_count": 1,
                "pdf_pages": [1],
                "selection_reasons": [],
                "feature_flags": [],
            }
        ],
    }

    report = detect_sample_manifest(
        manifest=manifest,
        documents={document.document_id: document},
        navigation={document.document_id: [section]},
        pageir_root=pageir_root,
        output_dir=tmp_path / "report",
        text_layer_enabled=False,
    )

    record = report["samples"][0]
    assert record["status"] == "warning"
    assert record["issues"]["READING_ORDER_AMBIGUOUS"] == 1
    assert {
        "severity": "warning",
        "code": "READING_ORDER_AMBIGUOUS",
        "message": "keyword test",
        "pdf_page": 1,
        "manual_page": "1-1",
    } in record["issue_details"]
    assert "\\" not in record["markdown_path"]


def test_manifest_detection_preserves_frozen_selection(monkeypatch, tmp_path):
    manifest = {
        "schema_version": "0.1",
        "kind": "semantic-regression-sample",
        "release": "R17",
        "summary": {"sample_count": 1},
        "documents": [],
        "samples": [
            {
                "sample_id": "PINNED-001",
                "document_id": "theory",
                "section_id": "23.37",
                "pdf_pages": [425, 426, 427],
            }
        ],
    }
    manifest_path = tmp_path / "frozen.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    captured = {}
    monkeypatch.setattr(
        "lsdyna_manual.regression_sampling._discover_documents",
        lambda _manuals_dir, _release: {},
    )
    monkeypatch.setattr(
        "lsdyna_manual.regression_sampling.load_navigation",
        lambda _intermediate_dir: {},
    )

    def fake_detect(**kwargs):
        captured["manifest"] = kwargs["manifest"]
        return {"summary": {"checked_count": 1}}

    monkeypatch.setattr(
        "lsdyna_manual.regression_sampling.detect_sample_manifest",
        fake_detect,
    )
    monkeypatch.setattr(
        "lsdyna_manual.regression_sampling.write_sampling_outputs",
        lambda frozen, _report, _output: captured.setdefault("written", frozen),
    )

    frozen, report = run_manifest_detection(
        manifest_path=manifest_path,
        manuals_dir=tmp_path / "manuals",
        release="R17",
        intermediate_dir=tmp_path / "intermediate",
        pageir_root=tmp_path / "pageir",
        output_dir=tmp_path / "output",
    )

    assert frozen == manifest
    assert captured["manifest"] == manifest
    assert captured["written"] == manifest
    assert report["summary"]["checked_count"] == 1

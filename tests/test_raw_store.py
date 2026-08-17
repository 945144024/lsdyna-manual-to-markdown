"""Tests for provider raw artifact persistence."""

import json

from lsdyna_manual.parser.raw_store import store_paddle_bundle
from lsdyna_manual.providers.base import ProviderJobResult


def test_store_paddle_bundle_splits_layout_results_by_page(tmp_path):
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4 minimal")

    raw_jsonl = "\n".join(
        [
            json.dumps(
                {
                    "result": {
                        "layoutParsingResults": [
                            {
                                "markdown": {"text": "# page one"},
                                "outputImages": {},
                            },
                            {
                                "markdown": {"text": "# page two"},
                                "outputImages": {},
                            },
                        ]
                    }
                }
            )
        ]
    )
    job_result = ProviderJobResult(
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        job_id="job-1",
        state="done",
        raw_jsonl_text=raw_jsonl,
        metadata={
            "job_data": {"state": "done"},
            "timing": {"total_seconds": 12.5},
        },
    )

    stored = store_paddle_bundle(
        job_result,
        root=tmp_path / "raw",
        document_id="keyword-volume-2",
        volume=2,
        pdf_pages=[197, 198],
        batch_id=1,
        input_pdf_path=input_pdf,
    )

    assert (stored.batch_dir / "raw_result.jsonl").exists()
    assert (stored.batch_dir / "job.json").exists()
    assert (stored.batch_dir / "page_map.json").exists()
    assert [a.pdf_page for a in stored.page_artifacts] == [197, 198]
    assert stored.page_artifacts[0].markdown_path.read_text() == "# page one"
    job_metadata = json.loads(stored.job_metadata_path.read_text())
    assert job_metadata["timing"] == {"total_seconds": 12.5}
    page_record = json.loads(stored.page_artifacts[0].json_path.read_text())
    assert page_record["pdf_page"] == 197
    assert page_record["layout_result"]["markdown"]["text"] == "# page one"


def test_store_paddle_bundle_rejects_page_count_mismatch(tmp_path):
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4 minimal")
    raw_jsonl = json.dumps({"result": {"layoutParsingResults": []}})
    job_result = ProviderJobResult(
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        job_id="job-1",
        state="done",
        raw_jsonl_text=raw_jsonl,
        metadata={},
    )
    import pytest

    with pytest.raises(ValueError, match="returned 0 page results"):
        store_paddle_bundle(
            job_result,
            root=tmp_path / "raw",
            document_id="keyword-volume-2",
            volume=2,
            pdf_pages=[197],
            batch_id=1,
            input_pdf_path=input_pdf,
        )


def test_job_metadata_redacts_signed_result_url(tmp_path):
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4 minimal")
    raw_jsonl = json.dumps(
        {
            "result": {
                "layoutParsingResults": [
                    {"markdown": {"text": "# one", "images": {}}}
                ]
            }
        }
    )
    job_result = ProviderJobResult(
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        job_id="job-1",
        state="done",
        raw_jsonl_text=raw_jsonl,
        metadata={
            "job_data": {
                "state": "done",
                "resultUrl": {
                    "jsonUrl": "https://example.invalid/result.json?authorization=SECRET"
                },
            }
        },
    )
    stored = store_paddle_bundle(
        job_result,
        root=tmp_path / "raw",
        document_id="keyword-volume-2",
        volume=2,
        pdf_pages=[197],
        batch_id=1,
        input_pdf_path=input_pdf,
    )
    persisted = stored.job_metadata_path.read_text(encoding="utf-8")
    assert "SECRET" not in persisted
    assert "authorization" not in persisted
    assert "<redacted>" in persisted

"""Tests for parse checkpoint / resume state."""

from lsdyna_manual.parser.parse_state import (
    BatchParseState,
    PageParseState,
    ParseStateStore,
)


def test_parse_state_store_roundtrip_and_resume(tmp_path):
    path = tmp_path / "parsing" / "state.json"
    store = ParseStateStore(path)

    store.set(
        PageParseState(
            document_id="keyword-volume-2",
            volume=2,
            pdf_page=197,
            status="raw_done",
            provider="paddleocr-vl-remote",
            model="PaddleOCR-VL-1.6",
            source_sha256="abc",
            semantic_config_hash="semantic-1",
            job_id="job-1",
        )
    )

    reloaded = ParseStateStore(path)
    assert reloaded.is_raw_done(
        "keyword-volume-2",
        197,
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        source_sha256="abc",
        semantic_config_hash="semantic-1",
    )
    assert not reloaded.is_raw_done(
        "keyword-volume-2",
        197,
        provider="other-provider",
        model="PaddleOCR-VL-1.6",
        source_sha256="abc",
        semantic_config_hash="semantic-1",
    )
    assert not reloaded.is_raw_done(
        "keyword-volume-2",
        197,
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        source_sha256="other-source",
        semantic_config_hash="semantic-1",
    )
    assert not reloaded.is_done(
        "keyword-volume-2",
        197,
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        source_sha256="abc",
        semantic_config_hash="semantic-1",
        adapter_identity="adapter-1",
        pageir_schema_version="0.1",
    )


def test_pageir_cache_identity_distinguishes_adapter_and_schema(tmp_path):
    path = tmp_path / "state.json"
    store = ParseStateStore(path)
    store.set(
        PageParseState(
            document_id="keyword-volume-2",
            volume=2,
            pdf_page=197,
            status="done",
            provider="paddleocr-vl-remote",
            model="PaddleOCR-VL-1.6",
            source_sha256="sha",
            semantic_config_hash="semantic",
            adapter_identity="adapter-1",
            pageir_schema_version="0.1",
        )
    )
    assert store.is_done(
        "keyword-volume-2",
        197,
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        source_sha256="sha",
        semantic_config_hash="semantic",
        adapter_identity="adapter-1",
        pageir_schema_version="0.1",
    )
    assert not store.is_done(
        "keyword-volume-2",
        197,
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        source_sha256="sha",
        semantic_config_hash="semantic",
        adapter_identity="adapter-2",
        pageir_schema_version="0.1",
    )
    assert not store.is_done(
        "keyword-volume-2",
        197,
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        source_sha256="sha",
        semantic_config_hash="semantic",
        adapter_identity="adapter-1",
        pageir_schema_version="0.2",
    )
    assert store.is_raw_done(
        "keyword-volume-2",
        197,
        provider="paddleocr-vl-remote",
        model="PaddleOCR-VL-1.6",
        source_sha256="sha",
        semantic_config_hash="semantic",
    )


def test_batch_job_checkpoint_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    store = ParseStateStore(path)
    store.set_batch(
        BatchParseState(
            document_id="keyword-volume-2",
            plan_batch_id=7,
            pdf_pages=(31, 32, 33),
            status="polling",
            provider="paddleocr-vl-remote",
            model="PaddleOCR-VL-1.6",
            source_sha256="source",
            semantic_config_hash="semantic",
            job_id="remote-job",
        )
    )

    reloaded = ParseStateStore(path)
    batch = reloaded.get_batch("keyword-volume-2", 7, [31, 32, 33])
    assert batch.status == "polling"
    assert batch.job_id == "remote-job"

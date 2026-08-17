"""Integration coverage for the resumable parse command pipeline."""

import json
from types import SimpleNamespace

from pypdf import PdfWriter

from lsdyna_manual.pipeline import run_parsing
from lsdyna_manual.providers.base import ProviderJobResult


class FakePaddleProvider:
    provider_name = "paddleocr-vl-remote"

    def __init__(self):
        self.config = SimpleNamespace(model="PaddleOCR-VL-1.6")
        self.calls = 0

    def semantic_identity(self):
        return "paddleocr-vl-remote:PaddleOCR-VL-1.6:test"

    def parse_pdf_batch(
        self,
        input_pdf_path,
        *,
        document_id,
        pdf_pages,
        volume=None,
        resume_job_id=None,
        on_progress=None,
    ):
        self.calls += 1
        job_id = resume_job_id or f"job-{self.calls}"
        if on_progress is not None:
            on_progress("submitted", {"job_id": job_id})
            on_progress("polling", {"job_id": job_id, "remote_state": "done"})
        pages = [
            {
                "markdown": {"text": f"# page {page}", "images": {}},
                "outputImages": {},
                "prunedResult": {"parsing_res_list": []},
            }
            for page in pdf_pages
        ]
        return ProviderJobResult(
            provider=self.provider_name,
            model=self.config.model,
            job_id=job_id,
            state="done",
            raw_jsonl_text=json.dumps(
                {"result": {"layoutParsingResults": pages}}
            ),
            metadata={"job_data": {"state": "done"}},
        )


def test_run_parsing_limits_pages_and_resumes_valid_checkpoint(tmp_path):
    manual = tmp_path / "LS-DYNA_Manual_Vol_II_R17.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    with open(manual, "wb") as handle:
        writer.write(handle)

    corpus = tmp_path / "corpus"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""manual:
  release: "R17"
  documents:
    - path: "{manual}"
parser:
  api_key: null
  max_batch_pages: 2
output:
  corpus_dir: "{corpus}"
""",
        encoding="utf-8",
    )
    intermediate = corpus / "intermediate" / "keyword-volume-2"
    intermediate.mkdir(parents=True)
    document = {
        "document_id": "keyword-volume-2",
        "manual_type": "keyword",
        "release": "R17",
        "volume": 2,
    }
    (intermediate / "pagemap.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "document": document,
                "pages": [
                    {"pdf_page": page, "manual_page": None, "evidence": None}
                    for page in (1, 2, 3)
                ],
            }
        ),
        encoding="utf-8",
    )
    (intermediate / "sectionmap.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "document": document,
                "sections": [
                    {
                        "section_id": "A",
                        "keyword_id": "A",
                        "name": "*A",
                        "volume": 2,
                        "kind": "keyword",
                        "parent_section_id": None,
                        "pdf_pages": [1, 2, 3],
                        "manual_pages": [None, None, None],
                        "document_id": "keyword-volume-2",
                        "section_number": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provider = FakePaddleProvider()
    events = []
    first = run_parsing(
        config,
        document_id="keyword-volume-2",
        max_pages=2,
        provider=provider,
        on_progress=events.append,
        log=lambda _message: None,
    )

    assert first.status == "success"
    assert first.total_pages == 2
    assert first.completed_pages == 2
    assert provider.calls == 1
    assert (corpus / "parsing" / "pageir" / "keyword-volume-2" / "page_000002.json").is_file()

    second = run_parsing(
        config,
        document_id="keyword-volume-2",
        max_pages=2,
        provider=provider,
        on_progress=events.append,
        log=lambda _message: None,
    )
    assert second.completed_pages == 2
    assert provider.calls == 1
    assert events[-1].phase == "checkpoint_validated"

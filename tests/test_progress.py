"""Terminal parse progress tests."""

from io import StringIO

from lsdyna_manual.parser.progress import ParseProgressEvent, TerminalParseProgress


def test_terminal_progress_is_page_based_and_resume_aware():
    stream = StringIO()
    progress = TerminalParseProgress(
        10, completed_pages=4, stream=stream, width=10
    )

    progress(
        ParseProgressEvent(
            phase="raw_done",
            document_id="keyword-volume-2",
            pdf_pages=(24, 25, 26),
            completed_delta=3,
        )
    )

    rendered = stream.getvalue()
    assert "7/10 pages" in rendered
    assert "pages=24-26" in rendered
    assert "[#######---]" in rendered
    assert "elapsed=" in rendered
    assert "rate=" in rendered
    assert "eta=" in rendered

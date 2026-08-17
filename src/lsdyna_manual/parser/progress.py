"""Progress events and a dependency-free terminal progress bar."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, TextIO


@dataclass(frozen=True)
class ParseProgressEvent:
    phase: str
    document_id: str | None = None
    batch_id: int | None = None
    pdf_pages: tuple[int, ...] = ()
    sections: tuple[str, ...] = ()
    completed_delta: int = 0
    job_id: str | None = None
    message: str | None = None


ParseProgressCallback = Callable[[ParseProgressEvent], None]


class TerminalParseProgress:
    """Render stable page-based progress while batch transport changes."""

    def __init__(
        self,
        total_pages: int,
        *,
        completed_pages: int = 0,
        stream: TextIO = sys.stderr,
        width: int = 28,
    ) -> None:
        self.total_pages = total_pages
        self.completed_pages = completed_pages
        self.stream = stream
        self.width = width
        self._tty = bool(getattr(stream, "isatty", lambda: False)())
        self._last_length = 0
        self._last_non_tty_phase: str | None = None

    def __call__(self, event: ParseProgressEvent) -> None:
        self.completed_pages = min(
            self.total_pages,
            self.completed_pages + max(event.completed_delta, 0),
        )
        line = self._line(event)
        if self._tty:
            padding = " " * max(self._last_length - len(line), 0)
            self.stream.write(f"\r{line}{padding}")
            self.stream.flush()
            self._last_length = len(line)
            return

        if (
            event.completed_delta
            or event.phase in {"paused_quota", "failed", "completed"}
            or event.phase != self._last_non_tty_phase
        ):
            self.stream.write(line + "\n")
            self.stream.flush()
            self._last_non_tty_phase = event.phase

    def finish(self, status: str) -> None:
        event = ParseProgressEvent(phase=status)
        if self._tty:
            self(event)
            self.stream.write("\n")
            self.stream.flush()
        elif status not in {"completed", "paused_quota", "failed"}:
            self(event)

    def _line(self, event: ParseProgressEvent) -> str:
        ratio = (
            self.completed_pages / self.total_pages
            if self.total_pages
            else 1.0
        )
        filled = min(self.width, int(ratio * self.width))
        bar = "#" * filled + "-" * (self.width - filled)
        context: list[str] = [event.phase]
        if event.document_id:
            context.append(event.document_id)
        if event.sections:
            context.append("section=" + ",".join(event.sections[:2]))
        if event.pdf_pages:
            first, last = event.pdf_pages[0], event.pdf_pages[-1]
            page_label = str(first) if first == last else f"{first}-{last}"
            context.append(f"pages={page_label}")
        return (
            f"[{bar}] {self.completed_pages}/{self.total_pages} pages "
            + " ".join(context)
        )

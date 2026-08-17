"""Real-PDF regression matrix runner for PageMap/SectionMap."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Iterable

from lsdyna_manual.documents import VERIFIED_RELEASES
from lsdyna_manual.parser.discovery import discover_documents
from lsdyna_manual.parser.ingest import sha256_of
from lsdyna_manual.parser.quality import evaluate_inspection
from lsdyna_manual.parser.segmentation import (
    InspectionResult,
    inspect_document,
    write_inspection_artifacts,
)
from lsdyna_manual.parser.text_extractor import PopplerLayoutExtractor


def review_pages(result: InspectionResult, limit: int = 24) -> list[int]:
    """Select stable, informative pages without random sampling."""
    pages: set[int] = {1, 2, 3, result.stats["pdf_pages"]}
    pages.update(
        issue.pdf_page
        for issue in result.issues
        if issue.pdf_page is not None
    )
    starts = sorted(
        section.pdf_pages[0]
        for section in result.sections
        if section.pdf_pages
    )
    if starts:
        positions = {
            0,
            len(starts) // 4,
            len(starts) // 2,
            (3 * len(starts)) // 4,
            len(starts) - 1,
        }
        pages.update(starts[position] for position in positions)
    for evidence in ("anchor", "interpolated", "footer"):
        evidence_pages = [
            entry.pdf_page
            for entry in result.pagemap
            if entry.evidence == evidence
        ]
        pages.update(evidence_pages[:2])
    return sorted(page for page in pages if 1 <= page <= result.stats["pdf_pages"])[:limit]


def render_review_pages(
    pdf_path: Path,
    pages: Iterable[int],
    output_dir: Path,
) -> list[Path]:
    """Render selected pages with Poppler for visual/model review."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for page in pages:
        output = output_dir / f"page_{page:06d}.png"
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(page),
                "-l",
                str(page),
                "-png",
                "-singlefile",
                str(pdf_path),
                str(output.with_suffix("")),
            ],
            check=True,
            capture_output=True,
        )
        rendered.append(output)
    return rendered


_BASELINE_IDENTITY_FIELDS = (
    "source_sha256",
    "pagemap_sha256",
    "sectionmap_sha256",
    "pdf_pages",
    "sections",
    "pagemap_filled",
)


def apply_review_baseline(records: list[dict], baseline: dict) -> None:
    """Apply prior model review only when source and outputs match exactly."""
    indexed = {
        (record["release"], record["document_id"]): record
        for record in baseline.get("documents", [])
    }
    review = baseline.get("review", {})

    for record in records:
        reviewed = indexed.get((record["release"], record["document_id"]))
        if reviewed is None or any(
            record.get(field) != reviewed.get(field)
            for field in _BASELINE_IDENTITY_FIELDS
        ):
            record["llm_review_status"] = "pending"
            continue
        record["llm_review_status"] = reviewed.get(
            "llm_review_status", "pending"
        )
        record["llm_reviewed_pdf_pages"] = reviewed.get(
            "llm_reviewed_pdf_pages", []
        )
        record["llm_review_method"] = review.get("method")
        record["llm_reviewed_at"] = review.get("reviewed_at")


def load_review_baseline(path: Path) -> dict:
    """Load a copyright-safe reviewed regression baseline."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "0.1":
        raise ValueError(f"unsupported regression baseline schema: {path}")
    return payload


def run_regression_matrix(
    manuals_dir: Path,
    output_dir: Path,
    releases: Iterable[str] = tuple(f"R{n}" for n in range(12, 18)),
    *,
    render: bool = False,
    baseline_path: Path | None = None,
) -> dict:
    """Run inspection for every discovered document in each requested release."""
    manuals_dir = Path(manuals_dir)
    output_dir = Path(output_dir)
    extractor = PopplerLayoutExtractor()
    records: list[dict] = []

    for release in releases:
        documents = discover_documents(manuals_dir, expected_release=release)
        release_dir = output_dir / release.lower()
        release_records: list[dict] = []
        results: list[InspectionResult] = []
        for document in documents:
            result = inspect_document(document, extractor)
            results.append(result)
            quality = evaluate_inspection(result)
            pages = review_pages(result)
            review_dir = release_dir / document.document_id / "review"
            if render:
                rendered = render_review_pages(
                    document.path,
                    pages,
                    review_dir,
                )
            else:
                rendered = [
                    review_dir / f"page_{page:06d}.png"
                    for page in pages
                    if (review_dir / f"page_{page:06d}.png").is_file()
                ]
            release_records.append(
                {
                    "release": release,
                    **document.metadata(),
                    "source_sha256": sha256_of(document.path),
                    **quality.metrics,
                    "parser_issue_codes": {
                        code: sum(1 for issue in result.issues if issue.code == code)
                        for code in sorted({issue.code for issue in result.issues})
                    },
                    "review_pages": pages,
                    "rendered_review_pages": [
                        str(path.relative_to(output_dir)) for path in rendered
                    ],
                    "llm_review_status": "pending",
                }
            )
        intermediate_dir = write_inspection_artifacts(
            results, release_dir / "intermediate"
        )
        for record in release_records:
            document_dir = intermediate_dir / record["document_id"]
            record["pagemap_sha256"] = sha256_of(
                document_dir / "pagemap.json"
            )
            record["sectionmap_sha256"] = sha256_of(
                document_dir / "sectionmap.json"
            )
        records.extend(release_records)

    if baseline_path is not None:
        apply_review_baseline(records, load_review_baseline(baseline_path))

    matrix = {
        "schema_version": "0.1",
        "verified_releases": sorted(VERIFIED_RELEASES),
        "manuals_dir": str(manuals_dir),
        "documents": records,
        "review_baseline": (
            str(baseline_path) if baseline_path is not None else None
        ),
        "summary": {
            "document_count": len(records),
            "failed_quality_gates": sum(
                record["quality_status"] == "failed" for record in records
            ),
            "pending_llm_reviews": sum(
                record["llm_review_status"] == "pending" for record in records
            ),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "regression_matrix.json").write_text(
        json.dumps(matrix, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return matrix


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manuals-dir", type=Path, default=Path("manuals"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("workspace/regression")
    )
    parser.add_argument(
        "--releases",
        nargs="+",
        default=[f"R{n}" for n in range(12, 18)],
    )
    parser.add_argument("--render", action="store_true")
    parser.add_argument(
        "--baseline",
        type=Path,
        help="reviewed baseline; exact matches inherit model-review status",
    )
    parser.add_argument(
        "--require-reviewed",
        action="store_true",
        help="fail when any document lacks a matching reviewed baseline",
    )
    args = parser.parse_args(argv)
    matrix = run_regression_matrix(
        args.manuals_dir,
        args.output_dir,
        args.releases,
        render=args.render,
        baseline_path=args.baseline,
    )
    print(
        f"processed {matrix['summary']['document_count']} documents; "
        f"quality failures={matrix['summary']['failed_quality_gates']}; "
        f"pending model reviews={matrix['summary']['pending_llm_reviews']}"
    )
    failed = bool(matrix["summary"]["failed_quality_gates"])
    if args.require_reviewed and matrix["summary"]["pending_llm_reviews"]:
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


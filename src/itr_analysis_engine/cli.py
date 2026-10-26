"""Command-line interface.

Examples::

    python -m itr_analysis_engine.cli analyze input/return.pdf --json output/analysis.json --pdf output/report.pdf
    python -m itr_analysis_engine.cli analyze return.json --assessment-year 2025-26
    python -m itr_analysis_engine.cli batch input/ --out-dir output/batch --pdf

Exit codes:
    0  analysis completed (see validation status in the output)
    1  analysis completed but review is required (only with --fail-on-review)
    2  usage error (bad arguments, missing file)
    3  at least one document could not be analysed (unsupported, OCR required, encrypted, corrupt)
    4  unexpected internal error

Only report IDs, statuses and indicator codes are printed - never PANs or
taxpayer details.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .analyzer import AnalysisOptions, AnalysisResult, analyze_file, analyze_paths
from .exceptions import MalformedValueError
from .normalizer import normalize_assessment_year
from .ocr import get_ocr_backend
from .risk import RuleConfig
from .security import PIIRedactingFilter, safe_filename

EXIT_OK, EXIT_REVIEW, EXIT_USAGE, EXIT_UNANALYZABLE, EXIT_ERROR = 0, 1, 2, 3, 4
SUPPORTED_SUFFIXES = {".pdf", ".json"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="itr-analyze", description="Deterministic Indian ITR document analysis (local only).")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show (redacted) debug logs.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--assessment-year", "--ay", dest="assessment_year", help="Expected AY, e.g. 2025-26.")
        p.add_argument("--rules", type=Path, help="JSON file with rule overrides (severity, thresholds, enabled).")
        p.add_argument("--ocr", choices=["none", "tesseract"], default="none", help="Local OCR backend for scanned PDFs.")
        p.add_argument("--max-mb", type=float, default=15.0, help="Maximum document size in MB (default 15).")
        p.add_argument("--strict-schemas", action="store_true", help="Reject JSON forms whose mapping is unverified.")
        p.add_argument("--fail-on-review", action="store_true", help="Exit 1 when a report needs manual review.")

    a = sub.add_parser("analyze", help="Analyse one document.")
    a.add_argument("file", type=Path)
    a.add_argument("--json", type=Path, help="Write analysis JSON here.")
    a.add_argument("--pdf", type=Path, help="Write the PDF report here.")
    common(a)

    b = sub.add_parser("batch", help="Analyse several documents (files and/or directories) with year-over-year comparison.")
    b.add_argument("paths", nargs="+", type=Path)
    b.add_argument("--out-dir", type=Path, required=True, help="Directory for per-document JSON / PDF and summary.json.")
    b.add_argument("--pdf", action="store_true", help="Also write a PDF report per document.")
    common(b)
    return parser


def _options(args: argparse.Namespace) -> AnalysisOptions:
    ay = normalize_assessment_year(args.assessment_year) if args.assessment_year else None
    return AnalysisOptions(
        assessment_year=ay,
        ocr_backend=get_ocr_backend(args.ocr),
        rules=RuleConfig.from_file(args.rules) if args.rules else RuleConfig(),
        max_bytes=int(args.max_mb * 1024 * 1024),
        allow_unverified_schemas=not args.strict_schemas,
    )


def _describe(result: AnalysisResult, label: str) -> str:
    r = result.report
    codes = ", ".join(i["code"] for i in r["risk_indicators"]) or "none"
    return (
        f"{label}: report_id={r['report_id']} ay={r['assessment_year'] or 'n/a'} "
        f"extraction={r['document']['extraction_status']} validation={r['validation']['status']} indicators=[{codes}]"
    )


def _exit_code(results: list[AnalysisResult], fail_on_review: bool) -> int:
    if any(not r.analyzable for r in results):
        return EXIT_UNANALYZABLE
    if fail_on_review and any(r.report["validation"]["status"] != "passed" for r in results):
        return EXIT_REVIEW
    return EXIT_OK


def _collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(str(p))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(PIIRedactingFilter())
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, handlers=[handler], force=True)

    try:
        options = _options(args)
    except (MalformedValueError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        if args.command == "analyze":
            if not args.file.is_file():
                print(f"error: file not found: {args.file}", file=sys.stderr)
                return EXIT_USAGE
            result = analyze_file(args.file, options)
            if args.json:
                result.write_json(args.json)
            if args.pdf:
                result.write_pdf(args.pdf)
            if not args.json and not args.pdf:
                print(result.to_json())
            print(_describe(result, args.file.name), file=sys.stderr)
            return _exit_code([result], args.fail_on_review)

        try:
            files = _collect(args.paths)
        except FileNotFoundError as exc:
            print(f"error: path not found: {exc}", file=sys.stderr)
            return EXIT_USAGE
        if not files:
            print("error: no .pdf or .json files found", file=sys.stderr)
            return EXIT_USAGE
        batch = analyze_paths(files, options)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for path, result in zip(files, batch.results):
            stem = f"{safe_filename(path.stem)}_{result.report_id[:8]}"
            result.write_json(args.out_dir / f"{stem}.json")
            if args.pdf:
                result.write_pdf(args.out_dir / f"{stem}.pdf")
            print(_describe(result, path.name), file=sys.stderr)
        (args.out_dir / "summary.json").write_text(json.dumps(batch.summary(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"summary: {args.out_dir / 'summary.json'}", file=sys.stderr)
        return _exit_code(batch.results, args.fail_on_review)
    except Exception as exc:  # pragma: no cover - last-resort guard
        logging.getLogger(__name__).exception("internal error")
        print(f"internal error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())

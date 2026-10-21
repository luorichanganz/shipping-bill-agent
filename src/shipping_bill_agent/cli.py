from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shipping_bill_agent import __version__
from shipping_bill_agent.config import Settings
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.normalization.charge_names import ChargeNameNormalizer
from shipping_bill_agent.ocr.client import OCRClient
from shipping_bill_agent.ocr.publisher import StaticDirectoryPublisher
from shipping_bill_agent.ocr.reviewer import OpenAIVisionOCRReviewer
from shipping_bill_agent.reporting import build_batch_report, render_text_report
from shipping_bill_agent.workflow.graph import ShippingBillAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shipping bill extraction and reconciliation agent")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="Process a bill or EML file")
    process.add_argument("--input", required=True, type=Path, help="EML, PDF, image, Excel or Word file")
    process.add_argument(
        "--ledger",
        type=Path,
        default=Path("data/ledgers/bills.json"),
        help="Existing bill ledger JSON",
    )
    process.add_argument("--accounting-entity", required=True, help="Current accounting entity")
    process.add_argument(
        "--extractor",
        choices=["auto", "heuristic", "llm"],
        default="auto",
        help="Extraction backend; auto uses LLM when LLM_API_KEY or OPENAI_API_KEY is available",
    )
    process.add_argument("--output", type=Path, help="Write result JSON to this path")
    process.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Console output format; structured JSON still includes the full report",
    )
    process.add_argument("--dry-run", action="store_true", help="Do not apply CREATE/UPDATE results")
    process.add_argument("--no-ocr", action="store_true", help="Disable OCR API calls")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if args.command != "process":
        return 2

    settings = Settings.from_env()
    extractor = args.extractor
    if extractor == "auto":
        extractor = "llm" if settings.openai_api_key else "heuristic"
    ocr_client = None
    ocr_reviewer = None
    if not args.no_ocr:
        publisher = StaticDirectoryPublisher(
            directory=settings.ocr_public_dir,
            base_url=settings.ocr_public_base_url,
        )
        ocr_client = OCRClient(
            endpoint=settings.ocr_endpoint,
            publisher=publisher,
            task=settings.ocr_task,
            connect_timeout=settings.ocr_connect_timeout,
            read_timeout=settings.ocr_read_timeout,
            max_retries=settings.ocr_max_retries,
        )
        if settings.vision_model:
            ocr_reviewer = OpenAIVisionOCRReviewer(settings)

    parser = DocumentParser(
        settings=settings,
        ocr_client=ocr_client,
        ocr_reviewer=ocr_reviewer,
    )
    charge_normalizer = (
        ChargeNameNormalizer.from_file(settings.charge_alias_file)
        if settings.charge_alias_file.exists()
        else ChargeNameNormalizer()
    )
    agent = ShippingBillAgent(
        settings=settings,
        ledger_path=args.ledger,
        extractor=extractor,
        parser=parser,
        charge_normalizer=charge_normalizer,
    )
    results = agent.run(args.input, args.accounting_entity)

    if not args.dry_run:
        applied = agent.ledger.apply_results(results)
        if applied:
            sys.stderr.write(f"Applied bill operations: {', '.join(applied)}\n")

    report = build_batch_report(results, args.input, dry_run=args.dry_run)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        structured = json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2)
        args.output.write_text(structured + "\n", encoding="utf-8")

    rendered = (
        json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2)
        if args.format == "json"
        else render_text_report(report)
    )
    sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

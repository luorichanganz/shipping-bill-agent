from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from shipping_bill_agent.config import Settings
from shipping_bill_agent.demo_data import write_sample_email
from shipping_bill_agent.extraction.heuristic import HeuristicExtractor
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.models.output import Operation
from shipping_bill_agent.storage.json_ledger import JsonLedger
from shipping_bill_agent.workflow.graph import ShippingBillAgent


def run_case(ledger_path: Path, email_path: Path):
    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings=settings,
        ledger_path=ledger_path,
        extractor="heuristic",
        parser=DocumentParser(settings),
    )
    return agent.run(email_path, accounting_entity="Origin Agent")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="shipping-eval-") as temp_dir:
        root = Path(temp_dir)
        sample = Path("data/ledgers/bills.json")
        ledger_path = root / "bills.json"
        ledger_path.write_bytes(sample.read_bytes())
        email_path = write_sample_email(root / "updated_bill.eml")

        first = run_case(ledger_path, email_path)
        JsonLedger(ledger_path).apply_results(first)
        second = run_case(ledger_path, email_path)

        checks = {
            "update_detected": first[0].operation == Operation.UPDATE,
            "amount_extracted": first[0].charge_details.amount == 40.0,
            "posting_type_ap": first[0].financial_attributes.posting_type == "AP",
            "charge_direction_extracted": first[0].charge_details.charge_direction == "CHARGE",
            "evidence_present": bool(first[0].audit_trail.evidence),
            "source_hash_recorded": bool(first[0].audit_trail.source_hashes),
            "rerun_no_change": second[0].operation == Operation.NO_CHANGE,
        }
        real_samples = Path("笔试题AIAGENT")
        if real_samples.exists():
            parser = DocumentParser(Settings())
            extractor = HeuristicExtractor()
            debit_source = parser.parse_path(real_samples / "PDF 示例账单2.pdf").sources[0]
            debit = extractor.extract(debit_source)
            checks["real_pdf_charge_count"] = len(debit.charges) == 3
            checks["real_pdf_total_matches"] = (
                sum((charge.amount for charge in debit.charges), start=0) == debit.total
            )

            arrival_source = parser.parse_path(real_samples / "PDF示例账单1.pdf").sources[0]
            arrival = extractor.extract(arrival_source)
            checks["real_arrival_charge"] = (
                len(arrival.charges) == 1
                and arrival.charges[0].currency == "USD"
                and arrival.charges[0].amount == 245
            )

            eml_path = next(real_samples.glob("*.eml"))
            eml_sources = parser.parse_path(eml_path).sources
            body = next(source for source in eml_sources if source.source_type == "eml")
            body_document = extractor.extract(body)
            checks["real_eml_attachment_count"] = (
                sum(source.source_type == "pdf" for source in eml_sources) == 2
            )
            checks["real_eml_charge"] = (
                len(body_document.charges) == 1
                and body_document.charges[0].amount == 50
                and body_document.charges[0].currency == "USD"
            )
        report = {
            "checks": checks,
            "passed": sum(checks.values()),
            "total": len(checks),
            "first_operation": str(first[0].operation),
            "second_operation": str(second[0].operation),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

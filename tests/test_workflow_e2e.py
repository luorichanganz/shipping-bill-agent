from __future__ import annotations

import hashlib
from email.message import EmailMessage
from pathlib import Path

from shipping_bill_agent.config import Settings
from shipping_bill_agent.demo_data import build_sample_email
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.models.output import Operation
from shipping_bill_agent.reporting import build_batch_report, render_text_report
from shipping_bill_agent.storage.json_ledger import JsonLedger
from shipping_bill_agent.workflow.graph import ShippingBillAgent


def _agent(ledger_path: Path) -> ShippingBillAgent:
    settings = Settings(ocr_public_dir=ledger_path.parent / "ocr", confidence_threshold=0.8)
    return ShippingBillAgent(
        settings=settings,
        ledger_path=ledger_path,
        extractor="heuristic",
        parser=DocumentParser(settings),
    )


def test_eml_update_then_rerun_is_no_change(tmp_path, sample_ledger_path) -> None:
    input_path = tmp_path / "updated_bill.eml"
    input_path.write_bytes(build_sample_email(amount=40))
    agent = _agent(sample_ledger_path)

    results = agent.run(input_path, accounting_entity="Origin Agent")
    assert len(results) == 1
    result = results[0]
    assert result.operation == Operation.UPDATE
    assert result.charge_details.raw_charge_item == "H/D Fee"
    assert result.charge_details.charge_item == "Handling Charge"
    assert result.charge_details.amount == 40.0
    assert result.financial_attributes.posting_type == "AP"
    assert result.audit_trail.changed_fields == ["amount"]
    assert result.audit_trail.evidence
    assert result.to_json_dict()["charge_details"]["amount"] == 40.0

    JsonLedger(sample_ledger_path).apply_results(results)
    second_results = _agent(sample_ledger_path).run(input_path, accounting_entity="Origin Agent")
    assert second_results[0].operation == Operation.NO_CHANGE
    assert second_results[0].audit_trail.duplicate_submission

    report = build_batch_report(second_results, input_path, dry_run=True)
    assert len(report.notices) == 1
    assert "重复提交" in report.notices[0].message
    assert "重复提交提示：" in render_text_report(report)


def test_unmatched_charge_creates_ledger_row(tmp_path) -> None:
    ledger_path = tmp_path / "empty.json"
    input_path = tmp_path / "new_bill.eml"
    input_path.write_bytes(build_sample_email(amount=40))

    results = _agent(ledger_path).run(input_path, accounting_entity="Origin Agent")
    assert results[0].operation == Operation.CREATE
    applied = JsonLedger(ledger_path).apply_results(results)
    assert len(applied) == 1
    assert JsonLedger(ledger_path).load()[0].amount == 40


def test_credit_note_direction_changes_posting_type(tmp_path) -> None:
    message = EmailMessage()
    message["Subject"] = "Credit Note CN-202609-001"
    message["From"] = "Destination Agent <billing@destination.example>"
    message["To"] = "Origin Agent <ops@origin.example>"
    message.set_content(
        "Document Type: Credit Note\n"
        "Reference No.: CN-202609-001\n"
        "Date: 2026-09-20\n"
        "Issuer: Destination Agent\n"
        "Recipient: Origin Agent\n"
        "H/D Fee    40.00 USD PP"
    )
    input_path = tmp_path / "credit_note.eml"
    input_path.write_bytes(message.as_bytes())

    results = _agent(tmp_path / "empty.json").run(
        input_path,
        accounting_entity="Destination Agent",
    )

    assert results[0].operation == Operation.CREATE
    assert results[0].charge_details.charge_direction == "CREDIT"
    assert results[0].financial_attributes.posting_type == "AP"


def test_same_source_with_changed_extraction_is_flagged_as_conflict(
    tmp_path,
    sample_bill,
) -> None:
    input_path = tmp_path / "same_source.eml"
    payload = build_sample_email(amount=40)
    input_path.write_bytes(payload)
    source_hash = hashlib.sha256(payload).hexdigest()
    conflicted_bill = sample_bill.model_copy(update={"source_hashes": [source_hash]})
    ledger_path = tmp_path / "bills.json"
    JsonLedger(ledger_path).save([conflicted_bill])

    results = _agent(ledger_path).run(input_path, accounting_entity="Origin Agent")

    assert results[0].operation == Operation.PENDING_REVIEW
    assert any("同一源文件" in warning for warning in results[0].audit_trail.warnings)


def test_charge_direction_conflict_is_not_auto_applied(
    tmp_path,
    sample_ledger_path,
) -> None:
    input_path = tmp_path / "credit_amount.eml"
    input_path.write_bytes(build_sample_email(amount=-40))

    results = _agent(sample_ledger_path).run(
        input_path,
        accounting_entity="Origin Agent",
    )

    assert results[0].operation == Operation.PENDING_REVIEW
    assert results[0].charge_details.charge_direction == "CREDIT"
    assert any("收费方向" in warning for warning in results[0].audit_trail.warnings)

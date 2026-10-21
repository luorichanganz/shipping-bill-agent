from __future__ import annotations

from email.message import EmailMessage

from shipping_bill_agent.cli import main
from shipping_bill_agent.config import Settings
from shipping_bill_agent.demo_data import build_sample_email
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.models.output import (
    AuditTrail,
    BatchStatus,
    ChargeDetails,
    DocumentInfo,
    Evidence,
    FinancialAttributes,
    Operation,
    OperationResult,
    TransactionParty,
)
from shipping_bill_agent.reporting import build_batch_report, render_text_report
from shipping_bill_agent.storage.json_ledger import JsonLedger
from shipping_bill_agent.workflow.graph import ShippingBillAgent


def _result(
    operation: Operation,
    *,
    charge: str,
    amount: float,
    warning: str | None = None,
    evidence: list[Evidence] | None = None,
) -> OperationResult:
    return OperationResult(
        operation=operation,
        document_info=DocumentInfo(
            document_type="Debit Note",
            reference_no="DN-202610-001",
        ),
        transaction_party=TransactionParty(
            issuer="Destination Agent",
            recipient="Origin Agent",
        ),
        charge_details=ChargeDetails(
            raw_charge_item=charge,
            charge_item=charge,
            amount=amount,
            currency="USD",
            charge_direction="CHARGE",
        ),
        financial_attributes=FinancialAttributes(
            accounting_entity="Origin Agent",
            posting_type="AP",
        ),
        audit_trail=AuditTrail(
            source_files=["PDF 示例账单1.pdf"],
            evidence=evidence or [],
            warnings=[warning] if warning else [],
            confidence=0.95,
        ),
    )


def test_batch_report_marks_partial_success_and_renders_review_actions() -> None:
    evidence = Evidence(
        source_id="pdf-001",
        block_id="pdf-001:page-1",
        quote="Detention Charge    1310.40 USD",
        field="raw_charge_item",
    )
    reason = "通过 document_reference+charge 匹配到 2 条候选账单，需人工复核"
    readable_reason = "按单据号与费用名称匹配到 2 条候选账单，需人工复核"
    report = build_batch_report(
        [
            _result(Operation.CREATE, charge="Handling Charge", amount=65),
            _result(
                Operation.PENDING_REVIEW,
                charge="Detention Charge",
                amount=1310.4,
                warning=reason,
                evidence=[evidence],
            ),
        ],
        "PDF 示例账单1.pdf",
        dry_run=False,
    )

    assert report.status == BatchStatus.COMPLETED_WITH_REVIEW_ITEMS
    assert report.summary.create_count == 1
    assert report.summary.pending_review_count == 1
    review = report.review_items[0]
    assert review.source_files == ["PDF 示例账单1.pdf"]
    assert review.charge_item == "Detention Charge"
    assert review.reasons == [readable_reason]
    assert review.evidence == [evidence]
    assert "未写入账单库" in review.actions_not_taken
    assert "确认应更新的账单编号" in review.suggested_next_step

    rendered = render_text_report(report)
    assert "状态：COMPLETED_WITH_REVIEW_ITEMS" in rendered
    assert "文件：PDF 示例账单1.pdf" in rendered
    assert "费用：Detention Charge，USD 1,310.40" in rendered
    assert readable_reason in rendered
    assert "依据：" in rendered
    assert "系统未做：未新增或更新账单记录；未写入账单库" in rendered
    assert "建议：确认应更新的账单编号" in rendered


def test_batch_report_is_completed_when_no_review_items() -> None:
    report = build_batch_report(
        [_result(Operation.CREATE, charge="Handling Charge", amount=65)],
        "PDF 示例账单2.pdf",
        dry_run=True,
    )

    assert report.status == BatchStatus.COMPLETED
    assert report.review_items == []
    rendered = render_text_report(report)
    assert "1 项可新增" in rendered
    assert "dry-run" in rendered


def test_cli_prints_pending_review_summary_and_exits_successfully(tmp_path, capsys) -> None:
    message = EmailMessage()
    message["Subject"] = "No bill attached"
    message["From"] = "Origin Agent <ops@origin.example>"
    message["To"] = "Destination Agent <billing@destination.example>"
    message.set_content("This email contains no bill or charge item.")
    input_path = tmp_path / "empty.eml"
    input_path.write_bytes(message.as_bytes())

    exit_code = main(
        [
            "process",
            "--input",
            str(input_path),
            "--ledger",
            str(tmp_path / "bills.json"),
            "--accounting-entity",
            "Origin Agent",
            "--extractor",
            "heuristic",
            "--no-ocr",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "状态：COMPLETED_WITH_REVIEW_ITEMS" in output
    assert "待确认项目：" in output
    assert "empty.eml" in output
    assert "原因：" in output
    assert "依据：" in output
    assert "系统未做：" in output
    assert "建议：" in output


def test_multiple_candidates_are_reported_with_match_reason(tmp_path, sample_bill) -> None:
    duplicate = sample_bill.model_copy(update={"bill_id": "BILL-DEMO-002"})
    ledger_path = tmp_path / "bills.json"
    JsonLedger(ledger_path).save([sample_bill, duplicate])
    input_path = tmp_path / "updated_bill.eml"
    input_path.write_bytes(build_sample_email(amount=40))

    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings,
        ledger_path,
        extractor="heuristic",
        parser=DocumentParser(settings),
    )
    results = agent.run(input_path, accounting_entity="Origin Agent")
    report = build_batch_report(results, input_path, dry_run=True)

    assert report.status == BatchStatus.COMPLETED_WITH_REVIEW_ITEMS
    assert len(report.review_items) == 1
    assert any("匹配到 2 条候选账单" in reason for reason in report.review_items[0].reasons)

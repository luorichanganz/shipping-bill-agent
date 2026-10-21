from __future__ import annotations

from email.message import EmailMessage

from shipping_bill_agent.config import Settings
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.models.output import Operation
from shipping_bill_agent.workflow.graph import ShippingBillAgent


def test_document_without_charges_returns_pending_review(tmp_path) -> None:
    message = EmailMessage()
    message["Subject"] = "No bill attached"
    message["From"] = "Origin Agent <ops@origin.example>"
    message["To"] = "Destination Agent <billing@destination.example>"
    message.set_content("This email contains no bill or charge item.")
    path = tmp_path / "empty.eml"
    path.write_bytes(message.as_bytes())

    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=DocumentParser(settings),
    )
    results = agent.run(path, accounting_entity="Origin Agent")
    assert len(results) == 1
    assert results[0].operation == Operation.PENDING_REVIEW
    assert results[0].charge_details.amount is None
    assert results[0].audit_trail.warnings

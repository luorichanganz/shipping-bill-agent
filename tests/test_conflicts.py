from __future__ import annotations

from decimal import Decimal

from shipping_bill_agent.extraction.consolidate import consolidate_documents
from shipping_bill_agent.extraction.heuristic import HeuristicExtractor
from shipping_bill_agent.models.bill import ExtractedCharge, ExtractedDocument
from shipping_bill_agent.models.document import BlockType, ContentBlock, SourceDocument
from shipping_bill_agent.rules.validation import validate_document


def _source(source_id: str, file_name: str, text: str = "") -> SourceDocument:
    return SourceDocument(
        source_id=source_id,
        source_type="text",
        file_name=file_name,
        sha256=source_id,
        blocks=(
            [
                ContentBlock(
                    block_id=f"{source_id}:text",
                    block_type=BlockType.TEXT,
                    text=text,
                )
            ]
            if text
            else []
        ),
    )


def test_heuristic_keeps_identical_charge_rows_and_validation_flags_them() -> None:
    text = (
        "Document Type: Debit Note\n"
        "Reference No.: DN-DUP-001\n"
        "Date: 2026-10-20\n"
        "Issuer: Alpha Shipping\n"
        "Recipient: Beta Logistics\n"
        "H/D Fee    40.00 USD\n"
        "H/D Fee    40.00 USD\n"
        "TOTAL 80.00 USD\n"
    )
    document = HeuristicExtractor().extract(_source("text-1", "duplicate.txt", text))

    assert len(document.charges) == 2
    assert [charge.amount for charge in document.charges] == [Decimal("40"), Decimal("40")]

    report = validate_document(document)
    duplicate_findings = [
        finding for finding in report.errors if finding.code == "duplicate_charge"
    ]
    assert {finding.charge_index for finding in duplicate_findings} == {0, 1}


def test_cross_source_value_conflict_is_preserved_and_blocks_processing() -> None:
    sources = [
        _source("eml-body", "bill.eml"),
        _source("pdf-attachment", "bill.pdf"),
    ]
    context = ExtractedDocument(
        document_type="Debit Note",
        reference_no="DN-CROSS-001",
        total=Decimal("50"),
        source_ids=["eml-body"],
        confidence=0.95,
    )
    bill = ExtractedDocument(
        document_type="Debit Note",
        reference_no="DN-CROSS-001",
        total=Decimal("40"),
        charges=[
            ExtractedCharge(
                raw_charge_item="H/D Fee",
                amount=Decimal("40"),
                currency="USD",
                charge_direction="CHARGE",
                confidence=0.95,
            )
        ],
        source_ids=["pdf-attachment"],
        confidence=0.95,
    )

    document = consolidate_documents(sources, [context, bill])[0]
    total_conflicts = [conflict for conflict in document.conflicts if conflict.field == "total"]

    assert len(total_conflicts) == 1
    assert total_conflicts[0].values == ["bill.pdf=40", "bill.eml=50"]
    assert "不同来源的合计不一致" in total_conflicts[0].message
    report = validate_document(document)
    conflict_finding = next(
        finding for finding in report.errors if finding.code == "cross_source_conflict"
    )
    assert conflict_finding.source_id == "pdf-attachment"
    assert conflict_finding.block_id == "cross-source:total"
    assert conflict_finding.quote == "bill.pdf=40 / bill.eml=50"

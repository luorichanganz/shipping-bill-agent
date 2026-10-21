from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from shipping_bill_agent.config import Settings
from shipping_bill_agent.extraction.heuristic import HeuristicExtractor
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.models.document import BlockType, ContentBlock, SourceDocument
from shipping_bill_agent.models.output import Operation
from shipping_bill_agent.storage.json_ledger import JsonLedger
from shipping_bill_agent.workflow.graph import ShippingBillAgent

SAMPLES = Path("笔试题AIAGENT")


class _FixedExtractor:
    def __init__(self, document):
        self.document = document

    def extract(self, source):
        return self.document.model_copy(deep=True)


def test_real_debit_note_extracts_three_charge_rows() -> None:
    path = SAMPLES / "PDF 示例账单2.pdf"
    source = DocumentParser(Settings()).parse_path(path).sources[0]
    document = HeuristicExtractor().extract(source)

    assert document.document_type == "Debit Note"
    assert document.reference_no == "DC-108478"
    assert document.document_date.isoformat() == "2026-10-24"
    assert document.issuer == "NEPTUNE SHIPPING LIMITED (LAX)"
    assert document.recipient == "湃乐多（上海）航运科技有限公司"
    assert document.issuer_source_refs
    assert document.recipient_source_refs
    assert document.issuer_confidence == 0.9
    assert document.recipient_confidence == 0.9
    assert document.bill_of_lading_no.startswith("MAEU275752922")
    assert document.container_no == "MRSU2571577"
    assert document.total == Decimal("1410.40")
    assert [charge.raw_charge_item for charge in document.charges] == [
        "HANDLING CHARGE",
        "4 ADD'L DETENTION FREE TIME CHARGE",
        "HANDLING CHARGE",
    ]
    assert [charge.amount for charge in document.charges] == [
        Decimal("65.00"),
        Decimal("1310.40"),
        Decimal("35.00"),
    ]
    assert all(charge.charge_direction == "CHARGE" for charge in document.charges)
    assert sum((charge.amount for charge in document.charges), Decimal("0")) == document.total


def test_real_arrival_notice_extracts_on_carriage_charge() -> None:
    path = SAMPLES / "PDF示例账单1.pdf"
    source = DocumentParser(Settings()).parse_path(path).sources[0]
    document = HeuristicExtractor().extract(source)

    assert document.document_type == "Arrival Notice"
    assert document.document_date.isoformat() == "2026-10-19"
    assert document.issuer == "NEPTUNE SHIPPING LIMITED"
    assert document.recipient == "CIMC WETRANS INTL LOG CO LTD"
    assert document.bill_of_lading_no == "CMDUCHN4053601"
    assert document.total == Decimal("245.0000")
    assert len(document.charges) == 1
    assert document.charges[0].raw_charge_item == "On Carriage Additional - US Em"
    assert document.charges[0].amount == Decimal("245.0000")
    assert document.charges[0].currency == "USD"
    assert document.charges[0].charge_direction == "CHARGE"


def test_real_eml_extracts_body_charge_and_bill_attachments() -> None:
    path = next(SAMPLES.glob("*.eml"))
    sources = DocumentParser(Settings()).parse_path(path).sources
    extractor = HeuristicExtractor()

    assert any(source.file_name == "4055331281 MBL.pdf" for source in sources)
    assert any(source.file_name == "4055331281 HBL.pdf" for source in sources)

    body = next(source for source in sources if source.source_type == "eml")
    document = extractor.extract(body)
    assert document.document_type == "Pre-Alert"
    assert document.bill_of_lading_no == "OOLU4055331281"
    assert document.container_no == "TIIU8975975"
    assert len(document.charges) == 1
    assert document.charges[0].raw_charge_item == "H/C"
    assert document.charges[0].amount == Decimal("50")
    assert document.charges[0].currency == "USD"
    assert document.charges[0].charge_direction == "CHARGE"


def test_zim_image_ocr_text_extracts_wharfage_charges() -> None:
    text = """INVOICE
ZIM INTEGRATED SHIPPING SERVICES CO., LLC
AS AGENTS FOR ZIM INTEGRATED SHIPPING SERVICES LTD
ALL AMERICA FORWARDING, INC.
DEBIT INVOICE: ORFR1618731 Invoice date 10/03/2025
Booking Number: ZIMUNGB1312144
B/L: ZIMUNGB1312144
Payment Due: PAY IMMEDIATELY
1 X 40 HC
JXLU4504463
WHARFAGE ADMINISTRATION FEE C 5.00 USD 5.00 1.0000 USD 5.00
WHARFAGE 2TT 2.85 USD 21.90 1.0000 USD 21.90
USD 26.90
"""
    source = SourceDocument(
        source_id="zim-image",
        source_type="image",
        file_name="图片示例.png",
        sha256="fixture",
        blocks=[
            ContentBlock(
                block_id="zim-image:ocr",
                block_type=BlockType.IMAGE_TEXT,
                text=text,
            )
        ],
    )
    document = HeuristicExtractor().extract(source)

    assert document.document_type == "Invoice"
    assert document.reference_no == "ORFR1618731"
    assert document.document_date.isoformat() == "2025-10-03"
    assert document.issuer.startswith("ZIM INTEGRATED SHIPPING")
    assert document.recipient == "ALL AMERICA FORWARDING, INC."
    assert document.booking_no == "ZIMUNGB1312144"
    assert document.container_no == "JXLU4504463"
    assert document.total == Decimal("26.90")
    assert [(charge.raw_charge_item, charge.amount) for charge in document.charges] == [
        ("WHARFAGE ADMINISTRATION FEE", Decimal("5.00")),
        ("WHARFAGE", Decimal("21.90")),
    ]
    assert all(charge.charge_direction == "CHARGE" for charge in document.charges)


def test_real_debit_note_produces_one_create_per_charge(tmp_path) -> None:
    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=DocumentParser(settings),
    )
    results = agent.run(
        SAMPLES / "PDF 示例账单2.pdf",
        accounting_entity="湃乐多（上海）航运科技有限公司",
    )

    assert [result.operation for result in results] == [
        Operation.CREATE,
        Operation.CREATE,
        Operation.CREATE,
    ]
    assert [result.charge_details.charge_item for result in results] == [
        "Handling Charge",
        "Detention Charge",
        "Handling Charge",
    ]
    assert sum(result.charge_details.amount for result in results) == 1410.4
    assert all(result.charge_details.charge_direction == "CHARGE" for result in results)
    assert all(result.financial_attributes.posting_type == "AP" for result in results)
    assert all(result.transaction_party.issuer == "NEPTUNE SHIPPING LIMITED (LAX)" for result in results)
    assert all(
        result.transaction_party.recipient == "湃乐多（上海）航运科技有限公司"
        for result in results
    )
    assert all(result.audit_trail.source_files == ["PDF 示例账单2.pdf"] for result in results)
    assert all(result.audit_trail.party_confidence == {"issuer": 0.9, "recipient": 0.9} for result in results)
    assert all(
        {"issuer", "recipient"} <= {evidence.field for evidence in result.audit_trail.evidence}
        for result in results
    )
    assert len(JsonLedger(tmp_path / "bills.json").apply_results(results)) == 3


@pytest.mark.parametrize(
    ("update", "warning"),
    [
        ({"recipient_source_refs": []}, "接收方"),
        ({"recipient_confidence": 0.6}, "识别置信度"),
    ],
)
def test_real_debit_note_ambiguous_party_evidence_stays_pending(
    tmp_path,
    update,
    warning,
) -> None:
    settings = Settings(confidence_threshold=0.8)
    parser = DocumentParser(settings)
    source = parser.parse_path(SAMPLES / "PDF 示例账单2.pdf").sources[0]
    document = HeuristicExtractor().extract(source).model_copy(update=update)
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=parser,
    )
    agent.extractor = _FixedExtractor(document)

    results = agent.run(
        SAMPLES / "PDF 示例账单2.pdf",
        accounting_entity="湃乐多（上海）航运科技有限公司",
    )

    assert all(result.operation == Operation.PENDING_REVIEW for result in results)
    assert all(
        any(warning in item for item in result.audit_trail.warnings)
        for result in results
    )


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="set RUN_LIVE_LLM_TESTS=1 to call the configured LLM",
)
def test_real_debit_note_llm_party_roles_and_posting_type(tmp_path) -> None:
    settings = Settings.from_env()
    if not settings.openai_api_key:
        pytest.skip("LLM_API_KEY is not configured")
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="llm",
        parser=DocumentParser(settings),
    )

    results = agent.run(
        SAMPLES / "PDF 示例账单2.pdf",
        accounting_entity="湃乐多（上海）航运科技有限公司",
    )

    assert len(results) == 3
    assert all(result.operation == Operation.CREATE for result in results)
    assert all(result.financial_attributes.posting_type == "AP" for result in results)
    assert all(
        result.transaction_party.issuer.startswith("NEPTUNE SHIPPING LIMITED")
        for result in results
    )
    assert all(
        result.transaction_party.recipient.startswith("湃乐多")
        for result in results
    )
    assert all(result.audit_trail.party_confidence["issuer"] >= 0.8 for result in results)
    assert all(result.audit_trail.party_confidence["recipient"] >= 0.8 for result in results)
    assert all(
        {"issuer", "recipient"} <= {evidence.field for evidence in result.audit_trail.evidence}
        for result in results
    )

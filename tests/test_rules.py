from decimal import Decimal

from shipping_bill_agent.models.bill import ExtractedCharge, ExtractedDocument
from shipping_bill_agent.models.document import BlockType, ContentBlock, SourceDocument
from shipping_bill_agent.rules.posting_type import determine_posting_type
from shipping_bill_agent.rules.validation import validate_document, validate_ocr_confusions


def test_posting_type_uses_accounting_entity() -> None:
    assert (
        determine_posting_type(
            "Destination Agent",
            "Destination Agent",
            "Origin Agent",
            "CHARGE",
        )[0]
        == "AR"
    )
    assert (
        determine_posting_type(
            "Origin Agent",
            "Destination Agent",
            "Origin Agent",
            "CHARGE",
        )[0]
        == "AP"
    )
    assert (
        determine_posting_type(
            "Third Party",
            "Destination Agent",
            "Origin Agent",
            "CHARGE",
        )[0]
        is None
    )
    assert determine_posting_type(None, "Destination Agent", "Origin Agent", "CHARGE")[0] is None


def test_posting_type_reverses_for_credit_direction_and_requires_direction() -> None:
    assert (
        determine_posting_type(
            "Destination Agent",
            "Destination Agent",
            "Origin Agent",
            "CREDIT",
        )[0]
        == "AP"
    )
    assert (
        determine_posting_type(
            "Origin Agent",
            "Destination Agent",
            "Origin Agent",
            "CREDIT",
        )[0]
        == "AR"
    )
    posting_type, warnings, reason = determine_posting_type(
        "Origin Agent",
        "Destination Agent",
        "Origin Agent",
        None,
    )
    assert posting_type is None
    assert warnings
    assert reason == "missing_charge_direction"


def test_total_is_used_when_subtotal_is_absent() -> None:
    document = ExtractedDocument(
        total=Decimal("50"),
        charges=[
            ExtractedCharge(
                raw_charge_item="Handling Charge",
                amount=Decimal("40"),
                currency="USD",
                confidence=0.95,
            )
        ],
    )
    report = validate_document(document)
    assert any(finding.code == "total_mismatch" for finding in report.errors)


def test_ocr_confused_money_characters_are_detected() -> None:
    document = ExtractedDocument(
        reference_no="DN-202609-001",
        charges=[
            ExtractedCharge(
                raw_charge_item="Handling Charge",
                amount=Decimal("40"),
                currency="USD",
                charge_direction="CHARGE",
                confidence=0.95,
            )
        ],
    )
    source = SourceDocument(
        source_id="image-1",
        source_type="image",
        file_name="bill.png",
        sha256="abc",
        blocks=[
            ContentBlock(
                block_id="ocr-test:ocr-text",
                block_type=BlockType.IMAGE_TEXT,
                text="Invoice amount USD 1O0.00\nAnother line USD 4I.00",
            )
        ],
    )

    findings = validate_ocr_confusions(document, [source])

    assert {finding.code for finding in findings} == {"ocr_confusable_character"}
    assert any("1O0.00" in finding.message for finding in findings)
    assert any("4I.00" in finding.message for finding in findings)
    assert all(finding.quote for finding in findings)


def test_clean_ocr_money_does_not_trigger_confusion_check() -> None:
    document = ExtractedDocument(
        reference_no="DN-202609-001",
        charges=[
            ExtractedCharge(
                raw_charge_item="Handling Charge",
                amount=Decimal("100"),
                currency="USD",
                charge_direction="CHARGE",
                confidence=0.95,
            )
        ],
    )
    source = SourceDocument(
        source_id="image-1",
        source_type="image",
        file_name="bill.png",
        sha256="abc",
        blocks=[
            ContentBlock(
                block_id="ocr-test:ocr-text",
                block_type=BlockType.IMAGE_TEXT,
                text="Invoice amount USD 100.00",
            )
        ],
    )

    assert validate_ocr_confusions(document, [source]) == []

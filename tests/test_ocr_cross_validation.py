from __future__ import annotations

from shipping_bill_agent.models.document import BlockType, ContentBlock, SourceDocument
from shipping_bill_agent.rules.validation import validate_pdf_ocr_consistency


def _pdf_text(amount: str = "65.00", issuer: str = "NEPTUNE SHIPPING LIMITED") -> str:
    return (
        "Document Type: Debit Note\n"
        "D/C No. : DC-108478\n"
        "D/C DATE 10-24-2026\n"
        f"BALANCE DUE TO {issuer} USD {amount}\n"
        f"TOTAL {amount} CURRENCY USD\n"
        f"HANDLING CHARGE - {amount} 1.00 C {amount}\n"
    )


def _pdf_source(embedded_text: str, ocr_text: str) -> SourceDocument:
    return SourceDocument(
        source_id="pdf-cross-check",
        source_type="pdf",
        file_name="cross-check.pdf",
        sha256="abc",
        blocks=[
            ContentBlock(
                block_id="pdf-cross-check:p1:text",
                block_type=BlockType.TEXT,
                text=embedded_text,
                page_no=1,
            ),
            ContentBlock(
                block_id="ocr-cross-check:ocr-text1",
                block_type=BlockType.IMAGE_TEXT,
                text=ocr_text,
                confidence=0.95,
            ),
        ],
    )


def test_pdf_embedded_and_ocr_text_matching_fields_pass() -> None:
    text = _pdf_text()
    errors, warnings = validate_pdf_ocr_consistency(_pdf_source(text, text))

    assert errors == []
    assert not any(finding.code == "pdf_ocr_field_mismatch" for finding in warnings)


def test_pdf_embedded_and_ocr_charge_mismatch_is_hard_error() -> None:
    errors, _ = validate_pdf_ocr_consistency(
        _pdf_source(_pdf_text("65.00"), _pdf_text("75.00"))
    )

    assert any(finding.code == "pdf_ocr_field_mismatch" for finding in errors)
    assert any("费用行" in finding.message for finding in errors)
    assert any("75.00" in finding.quote for finding in errors)


def test_pdf_embedded_and_ocr_party_mismatch_is_hard_error() -> None:
    errors, _ = validate_pdf_ocr_consistency(
        _pdf_source(
            _pdf_text(issuer="NEPTUNE SHIPPING LIMITED"),
            _pdf_text(issuer="OTHER SHIPPING LIMITED"),
        )
    )

    assert any(
        finding.code == "pdf_ocr_field_mismatch" and "签发方" in finding.message
        for finding in errors
    )

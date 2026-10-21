from __future__ import annotations

from shipping_bill_agent.config import Settings
from shipping_bill_agent.models.document import BlockType, ContentBlock, ParseResult, SourceDocument
from shipping_bill_agent.models.output import Operation
from shipping_bill_agent.workflow.graph import ShippingBillAgent


class _ParserWithOcrFailure:
    def parse_path(self, _path):
        source = SourceDocument(
            source_id="pdf-1",
            source_type="pdf",
            file_name="bill.pdf",
            sha256="abc",
            metadata={"warnings": ["OCR 识别失败: timeout"]},
            blocks=[
                ContentBlock(
                    block_id="pdf-1:p1:text",
                    block_type=BlockType.TEXT,
                    text=(
                        "Document Type: Debit Note\n"
                        "Reference No.: DN-202609-001\n"
                        "Date: 2026-09-20\n"
                        "Issuer: Destination Agent\n"
                        "Recipient: Origin Agent\n"
                        "H/D Fee    40.00 USD PP"
                    ),
                )
            ],
        )
        return ParseResult(sources=[source])


def test_ocr_failure_forces_pending_review(tmp_path) -> None:
    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=_ParserWithOcrFailure(),
    )
    results = agent.run("ignored.pdf", accounting_entity="Origin Agent")
    assert results[0].operation == Operation.PENDING_REVIEW
    assert results[0].charge_details.amount == 40.0
    assert any("OCR" in warning for warning in results[0].audit_trail.warnings)


class _ParserWithConfusedOcrText:
    def parse_path(self, _path):
        source = SourceDocument(
            source_id="image-1",
            source_type="image",
            file_name="bill.png",
            sha256="abc",
            metadata={"warnings": []},
            blocks=[
                ContentBlock(
                    block_id="ocr-test:ocr-text",
                    block_type=BlockType.IMAGE_TEXT,
                    text=(
                        "Document Type: Debit Note\n"
                        "Reference No.: DN-202609-001\n"
                        "Date: 2026-09-20\n"
                        "Issuer: Destination Agent\n"
                        "Recipient: Origin Agent\n"
                        "H/D Fee    40.00 USD PP\n"
                        "OCR uncertain amount USD 1O0.00"
                    ),
                )
            ],
        )
        return ParseResult(sources=[source])


def test_ocr_confusable_characters_force_pending_review(tmp_path) -> None:
    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=_ParserWithConfusedOcrText(),
    )

    results = agent.run("ignored.png", accounting_entity="Origin Agent")

    assert results[0].operation == Operation.PENDING_REVIEW
    assert any("混淆" in warning for warning in results[0].audit_trail.warnings)
    assert any(
        evidence.field == "ocr_confusable_character"
        for evidence in results[0].audit_trail.evidence
    )


class _ParserWithLowConfidenceOcr:
    def parse_path(self, _path):
        source = SourceDocument(
            source_id="image-low-confidence",
            source_type="image",
            file_name="low-confidence.png",
            sha256="abc",
            metadata={"warnings": []},
            blocks=[
                ContentBlock(
                    block_id="ocr-low:ocr-text1",
                    block_type=BlockType.IMAGE_TEXT,
                    text=(
                        "Document Type: Debit Note\n"
                        "Reference No.: DN-202609-001\n"
                        "Date: 2026-09-20\n"
                        "Issuer: Destination Agent\n"
                        "Recipient: Origin Agent\n"
                        "H/D Fee    40.00 USD PP"
                    ),
                    confidence=0.6,
                )
            ],
        )
        return ParseResult(sources=[source])


def test_low_ocr_confidence_forces_pending_review(tmp_path) -> None:
    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=_ParserWithLowConfidenceOcr(),
    )

    results = agent.run("ignored.png", accounting_entity="Origin Agent")

    assert results[0].operation == Operation.PENDING_REVIEW
    assert results[0].audit_trail.ocr_confidence == 0.6
    assert any("OCR 置信度" in warning for warning in results[0].audit_trail.warnings)
    assert any(
        evidence.field == "low_ocr_confidence"
        for evidence in results[0].audit_trail.evidence
    )


class _ParserWithPdfOcrMismatch:
    def parse_path(self, _path):
        def bill_text(amount: str) -> str:
            return (
                "Document Type: Debit Note\n"
                "Reference No.: DN-202609-001\n"
                "Date: 2026-09-20\n"
                "Issuer: Destination Agent\n"
                "Recipient: Origin Agent\n"
                f"H/D Fee    {amount} USD PP"
            )

        source = SourceDocument(
            source_id="pdf-ocr-mismatch",
            source_type="pdf",
            file_name="mismatch.pdf",
            sha256="abc",
            metadata={"warnings": []},
            blocks=[
                ContentBlock(
                    block_id="pdf-ocr-mismatch:p1:text",
                    block_type=BlockType.TEXT,
                    text=bill_text("40.00"),
                ),
                ContentBlock(
                    block_id="ocr-mismatch:ocr-text1",
                    block_type=BlockType.IMAGE_TEXT,
                    text=bill_text("50.00"),
                    confidence=0.95,
                ),
            ],
        )
        return ParseResult(sources=[source])


def test_pdf_ocr_field_mismatch_forces_pending_review(tmp_path) -> None:
    settings = Settings(confidence_threshold=0.8)
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=_ParserWithPdfOcrMismatch(),
    )

    results = agent.run("ignored.pdf", accounting_entity="Origin Agent")

    assert all(result.operation == Operation.PENDING_REVIEW for result in results)
    assert any(
        "内嵌文本与 OCR 文本中不一致" in warning
        for result in results
        for warning in result.audit_trail.warnings
    )
    assert any(
        evidence.field == "pdf_ocr_field_mismatch"
        for result in results
        for evidence in result.audit_trail.evidence
    )

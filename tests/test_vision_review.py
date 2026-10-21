from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from shipping_bill_agent.config import Settings
from shipping_bill_agent.ingestion.context import ParserContext
from shipping_bill_agent.ingestion.pdf import parse_pdf_bytes
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.models.document import BlockType, ContentBlock
from shipping_bill_agent.models.output import Operation
from shipping_bill_agent.ocr.reviewer import (
    OpenAIVisionOCRReviewer,
    VisionOCRReviewer,
    VisionRecognition,
)
from shipping_bill_agent.workflow.graph import ShippingBillAgent

BILL_TEXT = (
    "Document Type: Debit Note\n"
    "Reference No.: DN-202609-001\n"
    "Date: 2026-09-20\n"
    "Issuer: Destination Agent\n"
    "Recipient: Origin Agent\n"
    "H/D Fee    40.00 USD PP"
)


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeOCR:
    def __init__(self, blocks: list[ContentBlock]):
        self.blocks = blocks

    def recognize_bytes(self, data, file_name, mime_type):
        return self.blocks


class _FakeVisionReviewer(VisionOCRReviewer):
    def __init__(self, settings: Settings, result: VisionRecognition):
        super().__init__(settings)
        self.result = result
        self.calls: list[str] = []

    def _recognize(self, png: bytes, ocr_text: str) -> VisionRecognition:
        self.calls.append(ocr_text)
        return self.result


def _settings() -> Settings:
    return Settings(
        confidence_threshold=0.8,
        vision_model="test-vision-model",
        vision_api_key="test-key",
    )


def test_only_low_confidence_ocr_blocks_trigger_vision_review() -> None:
    low = ContentBlock(
        block_id="ocr-low",
        block_type=BlockType.IMAGE_TEXT,
        text="H/D Fee 4O.00 USD",
        confidence=0.6,
    )
    high = ContentBlock(
        block_id="ocr-high",
        block_type=BlockType.IMAGE_TEXT,
        text="Document Type: Debit Note",
        confidence=0.95,
    )
    reviewer = _FakeVisionReviewer(
        _settings(),
        VisionRecognition(recognized_text=BILL_TEXT, confidence=0.93),
    )

    outcome = reviewer.review_image_bytes(_image_bytes(), "image-test", [low, high])

    assert reviewer.calls == ["H/D Fee 4O.00 USD\nDocument Type: Debit Note"]
    assert outcome.replaced_block_ids == {"ocr-low", "ocr-high"}
    assert outcome.reviewed_blocks[0].confidence == 0.93
    assert outcome.records[0]["accepted"] is True


def test_low_vision_confidence_keeps_original_ocr() -> None:
    low = ContentBlock(
        block_id="ocr-low",
        block_type=BlockType.IMAGE_TEXT,
        text="H/D Fee 4O.00 USD",
        confidence=0.6,
    )
    reviewer = _FakeVisionReviewer(
        _settings(),
        VisionRecognition(recognized_text="H/D Fee 40.00 USD", confidence=0.7),
    )

    outcome = reviewer.review_image_bytes(_image_bytes(), "image-test", [low])

    assert outcome.replaced_block_ids == set()
    assert outcome.reviewed_blocks == []
    assert any("保留原 OCR" in warning for warning in outcome.warnings)


def test_openai_vision_reviewer_parses_fenced_json_response() -> None:
    result = OpenAIVisionOCRReviewer._parse_recognition(
        '```json\n{"recognized_text": "H/D Fee 40.00 USD", '
        '"confidence": 0.96, "notes": null}\n```'
    )

    assert result.recognized_text == "H/D Fee 40.00 USD"
    assert result.confidence == 0.96


def test_pdf_low_confidence_block_is_replaced_by_vision_review() -> None:
    path = Path("笔试题AIAGENT") / "PDF 示例账单2.pdf"
    low = ContentBlock(
        block_id="ocr-pdf-low",
        block_type=BlockType.IMAGE_TEXT,
        text="HANDLING CHARGE 6S.00 USD",
        page_no=1,
        confidence=0.6,
    )
    reviewer = _FakeVisionReviewer(
        _settings(),
        VisionRecognition(recognized_text=BILL_TEXT, confidence=0.92),
    )
    source = parse_pdf_bytes(
        path.read_bytes(),
        path.name,
        ParserContext(
            settings=_settings(),
            ocr_client=_FakeOCR([low]),
            ocr_reviewer=reviewer,
        ),
    )[0]

    assert reviewer.calls == ["HANDLING CHARGE 6S.00 USD"]
    assert all(block.block_id != "ocr-pdf-low" for block in source.blocks)
    assert any(":vision-review-" in block.block_id for block in source.blocks)
    assert source.metadata["ocr_reviews"][0]["accepted"] is True


def test_successful_vision_review_clears_low_ocr_gate(tmp_path) -> None:
    settings = _settings()
    low = ContentBlock(
        block_id="ocr-image-low",
        block_type=BlockType.IMAGE_TEXT,
        text=BILL_TEXT,
        confidence=0.6,
    )
    reviewer = _FakeVisionReviewer(
        settings,
        VisionRecognition(recognized_text=BILL_TEXT, confidence=0.92),
    )
    image_path = tmp_path / "bill.png"
    image_path.write_bytes(_image_bytes())
    parser = DocumentParser(
        settings,
        ocr_client=_FakeOCR([low]),
        ocr_reviewer=reviewer,
    )
    agent = ShippingBillAgent(
        settings,
        tmp_path / "bills.json",
        extractor="heuristic",
        parser=parser,
    )

    results = agent.run(image_path, accounting_entity="Origin Agent")

    assert results[0].operation == Operation.CREATE
    assert results[0].financial_attributes.posting_type == "AP"
    assert results[0].audit_trail.ocr_confidence == 0.92
    assert results[0].audit_trail.ocr_reviews[0]["accepted"] is True

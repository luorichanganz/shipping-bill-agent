from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Any

import pdfplumber
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from shipping_bill_agent.config import Settings
from shipping_bill_agent.models.document import BlockType, ContentBlock

REVIEW_PROMPT = """你是航运单据 OCR 复核器。请直接查看图片，并结合低置信度 OCR 文本，
只转录图片中实际可见的内容，不得补造字段。

要求：
1. 优先保证金额、币种、日期、单号、提单号、订舱号和箱号的字符准确。
2. recognized_text 保留图片中的行结构和表格列关系，不要添加解释。
3. confidence 表示你对整段转录的置信度，范围 0 到 1。
4. 如果图片模糊、关键字段被遮挡或无法可靠判断，降低 confidence，并在 notes 中说明。
5. 不要输出主管推断或业务结论。
6. 只返回一个 JSON 对象，不要使用 Markdown 代码块：
   {"recognized_text": "...", "confidence": 0.0, "notes": null}

低置信度 OCR 文本：
__OCR_TEXT__
"""


class VisionRecognition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recognized_text: str
    confidence: float = Field(ge=0, le=1)
    notes: str | None = None


@dataclass
class OCRReviewOutcome:
    reviewed_blocks: list[ContentBlock] = field(default_factory=list)
    replaced_block_ids: set[str] = field(default_factory=set)
    records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class VisionOCRReviewer:
    def __init__(self, settings: Settings):
        if not settings.vision_model:
            raise ValueError("视觉 OCR 复核需要 VISION_MODEL")
        if not settings.vision_api_key:
            raise ValueError("视觉 OCR 复核需要 VISION_API_KEY 或通用 LLM_API_KEY")
        self.settings = settings

    def review_pdf_bytes(
        self,
        data: bytes,
        source_id: str,
        blocks: list[ContentBlock],
    ) -> OCRReviewOutcome:
        low_confidence = self._low_confidence_blocks(blocks)
        outcome = OCRReviewOutcome()
        if not low_confidence:
            return outcome

        grouped: dict[int, list[ContentBlock]] = {}
        for block in low_confidence:
            grouped.setdefault(block.page_no or 1, []).append(block)

        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page_no in grouped:
                    if page_no < 1 or page_no > len(pdf.pages):
                        outcome.warnings.append(
                            f"视觉复核跳过不存在的 PDF 页 {page_no}"
                        )
                        continue
                    rendered = pdf.pages[page_no - 1].to_image(resolution=144).original
                    png = self._prepare_image(rendered)
                    review_blocks = [
                        block
                        for block in blocks
                        if (block.page_no or 1) == page_no
                    ]
                    self._review_group(
                        outcome,
                        png,
                        review_blocks,
                        source_id,
                        page_no,
                    )
        except Exception as exc:
            outcome.warnings.append(f"视觉 OCR 复核失败: {exc}")
        return outcome

    def review_image_bytes(
        self,
        data: bytes,
        source_id: str,
        blocks: list[ContentBlock],
    ) -> OCRReviewOutcome:
        low_confidence = self._low_confidence_blocks(blocks)
        outcome = OCRReviewOutcome()
        if not low_confidence:
            return outcome
        try:
            with Image.open(io.BytesIO(data)) as image:
                png = self._prepare_image(image)
            self._review_group(
                outcome,
                png,
                blocks,
                source_id,
                None,
            )
        except Exception as exc:
            outcome.warnings.append(f"视觉 OCR 复核失败: {exc}")
        return outcome

    def _review_group(
        self,
        outcome: OCRReviewOutcome,
        png: bytes,
        blocks: list[ContentBlock],
        source_id: str,
        page_no: int | None,
    ) -> None:
        ocr_text = "\n".join(self._block_text(block) for block in blocks).strip()
        if not ocr_text:
            outcome.warnings.append("低置信度 OCR 块没有可复核文本")
            return
        try:
            result = self._recognize(png, ocr_text)
        except Exception as exc:
            outcome.warnings.append(f"视觉 OCR 复核失败: {exc}")
            return

        record = {
            "page_no": page_no,
            "original_block_ids": [block.block_id for block in blocks],
            "ocr_text": ocr_text[:2000],
            "vision_text": result.recognized_text[:2000],
            "ocr_confidence": min(
                (block.confidence for block in blocks if block.confidence is not None),
                default=None,
            ),
            "vision_confidence": result.confidence,
            "accepted": False,
            "notes": result.notes,
        }
        if (
            result.recognized_text.strip()
            and result.confidence >= self.settings.confidence_threshold
        ):
            reviewed_block = ContentBlock(
                block_id=f"{source_id}:vision-review-{len(outcome.records) + 1}",
                block_type=BlockType.IMAGE_TEXT,
                text=result.recognized_text.strip(),
                page_no=page_no,
                confidence=result.confidence,
            )
            outcome.reviewed_blocks.append(reviewed_block)
            outcome.replaced_block_ids.update(block.block_id for block in blocks)
            record["accepted"] = True
            record["reviewed_block_id"] = reviewed_block.block_id
        else:
            outcome.warnings.append(
                "视觉二次识别置信度不足或未返回文本，保留原 OCR 结果并进入复核"
            )
        outcome.records.append(record)

    def _recognize(self, png: bytes, ocr_text: str) -> VisionRecognition:
        raise NotImplementedError

    def _prepare_image(self, image: Image.Image) -> bytes:
        prepared = image.convert("RGB")
        prepared.thumbnail(
            (
                self.settings.vision_max_image_dimension,
                self.settings.vision_max_image_dimension,
            ),
            Image.Resampling.LANCZOS,
        )
        buffer = io.BytesIO()
        prepared.save(buffer, format="PNG")
        return buffer.getvalue()

    def _low_confidence_blocks(self, blocks: list[ContentBlock]) -> list[ContentBlock]:
        return [
            block
            for block in blocks
            if (
                block.confidence is not None
                and block.confidence < self.settings.confidence_threshold
            )
        ]

    @staticmethod
    def _block_text(block: ContentBlock) -> str:
        if block.text:
            return block.text
        return "\n".join(" | ".join(cell or "" for cell in row) for row in block.cells)


class OpenAIVisionOCRReviewer(VisionOCRReviewer):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": settings.vision_model,
            "temperature": settings.vision_temperature,
            "api_key": settings.vision_api_key,
        }
        if settings.vision_base_url:
            kwargs["base_url"] = settings.vision_base_url
        kwargs["timeout"] = settings.ocr_read_timeout
        self.model = ChatOpenAI(**kwargs)

    def _recognize(self, png: bytes, ocr_text: str) -> VisionRecognition:
        from langchain_core.messages import HumanMessage, SystemMessage

        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        response = self.model.invoke(
            [
                SystemMessage(content=REVIEW_PROMPT.replace("__OCR_TEXT__", ocr_text)),
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": "请复核图片并返回结构化转录结果。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ]
                ),
            ]
        )
        return self._parse_recognition(response.content)

    @staticmethod
    def _parse_recognition(content: Any) -> VisionRecognition:
        if isinstance(content, VisionRecognition):
            return content
        if isinstance(content, dict):
            return VisionRecognition.model_validate(content)
        text = str(content).strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```JSON").removeprefix("```")
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("视觉模型未返回 JSON 对象")
        return VisionRecognition.model_validate(json.loads(text[start : end + 1]))

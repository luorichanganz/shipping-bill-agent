from __future__ import annotations

import hashlib
import io

from PIL import Image

from shipping_bill_agent.ingestion.context import ParserContext
from shipping_bill_agent.models.document import SourceDocument


def parse_image_bytes(data: bytes, file_name: str, context: ParserContext) -> list[SourceDocument]:
    digest = hashlib.sha256(data).hexdigest()
    source_id = f"image-{digest[:16]}"
    warnings: list[str] = []
    metadata: dict[str, object] = {}
    review_records: list[dict[str, object]] = []
    try:
        with Image.open(io.BytesIO(data)) as image:
            metadata.update({"format": image.format, "width": image.width, "height": image.height})
            mime_type = Image.MIME.get(image.format or "", "application/octet-stream")
    except Exception as exc:
        warnings.append(f"图片元数据读取失败: {exc}")
        mime_type = "application/octet-stream"

    blocks = []
    if context.ocr_client is not None:
        try:
            blocks = context.ocr_client.recognize_bytes(data, file_name, mime_type)
            if not blocks:
                warnings.append("OCR 未返回可识别内容")
            elif context.ocr_reviewer is not None:
                outcome = context.ocr_reviewer.review_image_bytes(data, source_id, blocks)
                if outcome.replaced_block_ids:
                    blocks = [
                        block
                        for block in blocks
                        if block.block_id not in outcome.replaced_block_ids
                    ]
                    blocks.extend(outcome.reviewed_blocks)
                warnings.extend(outcome.warnings)
                review_records.extend(outcome.records)
        except Exception as exc:
            warnings.append(f"OCR 识别失败: {exc}")
    else:
        warnings.append("未配置 OCR 客户端，图片无法提取文字")

    metadata["warnings"] = warnings
    metadata["ocr_reviews"] = review_records
    return [
        SourceDocument(
            source_id=source_id,
            source_type="image",
            file_name=file_name,
            mime_type=mime_type,
            sha256=digest,
            parent_source_id=None,
            metadata=metadata,
            blocks=blocks,
        )
    ]

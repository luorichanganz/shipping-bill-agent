from __future__ import annotations

import hashlib
import io

import pdfplumber

from shipping_bill_agent.ingestion.context import ParserContext
from shipping_bill_agent.models.document import BlockType, ContentBlock, SourceDocument


def parse_pdf_bytes(data: bytes, file_name: str, context: ParserContext) -> list[SourceDocument]:
    digest = hashlib.sha256(data).hexdigest()
    source_id = f"pdf-{digest[:16]}"
    blocks: list[ContentBlock] = []
    warnings: list[str] = []
    review_records: list[dict[str, object]] = []

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    blocks.append(
                        ContentBlock(
                            block_id=f"{source_id}:p{page_index}:text",
                            block_type=BlockType.TEXT,
                            text=text,
                            page_no=page_index,
                        )
                    )
                for table_index, table in enumerate(page.extract_tables(), start=1):
                    cells = [
                        [str(cell).strip() if cell is not None else None for cell in row] for row in table
                    ]
                    if any(any(cell for cell in row) for row in cells):
                        blocks.append(
                            ContentBlock(
                                block_id=f"{source_id}:p{page_index}:table{table_index}",
                                block_type=BlockType.TABLE,
                                cells=cells,
                                page_no=page_index,
                            )
                        )
    except Exception as exc:
        warnings.append(f"PDF 内嵌内容解析失败: {exc}")

    if context.ocr_client is not None:
        try:
            ocr_blocks = context.ocr_client.recognize_bytes(data, file_name, "application/pdf")
            if ocr_blocks:
                if context.ocr_reviewer is not None:
                    outcome = context.ocr_reviewer.review_pdf_bytes(data, source_id, ocr_blocks)
                    if outcome.replaced_block_ids:
                        ocr_blocks = [
                            block
                            for block in ocr_blocks
                            if block.block_id not in outcome.replaced_block_ids
                        ]
                        ocr_blocks.extend(outcome.reviewed_blocks)
                    warnings.extend(outcome.warnings)
                    review_records.extend(outcome.records)
                blocks.extend(ocr_blocks)
            else:
                warnings.append("OCR 未返回可识别内容")
        except Exception as exc:
            warnings.append(f"OCR 识别失败: {exc}")
    else:
        warnings.append("未配置 OCR 客户端，仅使用 PDF 内嵌文本")

    return [
        SourceDocument(
            source_id=source_id,
            source_type="pdf",
            file_name=file_name,
            mime_type="application/pdf",
            sha256=digest,
            parent_source_id=None,
            metadata={"warnings": warnings, "ocr_reviews": review_records},
            blocks=blocks,
        )
    ]

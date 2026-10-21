from __future__ import annotations

from pathlib import Path

from shipping_bill_agent.config import Settings
from shipping_bill_agent.ingestion.context import ParserContext
from shipping_bill_agent.ingestion.eml import parse_eml_bytes
from shipping_bill_agent.ingestion.excel import parse_excel_bytes
from shipping_bill_agent.ingestion.exceptions import DocumentParseError
from shipping_bill_agent.ingestion.image import parse_image_bytes
from shipping_bill_agent.ingestion.pdf import parse_pdf_bytes
from shipping_bill_agent.ingestion.word import parse_word_bytes
from shipping_bill_agent.models.document import ParseResult, SourceDocument
from shipping_bill_agent.ocr.client import OCRClient
from shipping_bill_agent.ocr.reviewer import VisionOCRReviewer


class DocumentParser:
    def __init__(
        self,
        settings: Settings,
        ocr_client: OCRClient | None = None,
        ocr_reviewer: VisionOCRReviewer | None = None,
    ):
        self.settings = settings
        self.ocr_client = ocr_client
        self.ocr_reviewer = ocr_reviewer

    def parse_path(self, path: str | Path) -> ParseResult:
        input_path = Path(path)
        if not input_path.is_file():
            raise DocumentParseError(f"输入文件不存在: {input_path}")
        context = ParserContext(
            settings=self.settings,
            ocr_client=self.ocr_client,
            ocr_reviewer=self.ocr_reviewer,
        )
        data = input_path.read_bytes()
        sources = self.parse_bytes(
            data,
            input_path.name,
            mime_type=None,
            parent_source_id=None,
            depth=0,
            context=context,
        )
        return ParseResult(sources=sources, warnings=context.warnings)

    def parse_bytes(
        self,
        data: bytes,
        file_name: str,
        mime_type: str | None,
        parent_source_id: str | None,
        depth: int,
        context: ParserContext | None = None,
    ) -> list[SourceDocument]:
        context = context or ParserContext(
            settings=self.settings,
            ocr_client=self.ocr_client,
            ocr_reviewer=self.ocr_reviewer,
        )
        suffix = Path(file_name).suffix.lower()
        content_type = (mime_type or "").lower()

        if suffix == ".eml" or content_type == "message/rfc822":
            sources = parse_eml_bytes(data, file_name, context, self, parent_source_id, depth)
        elif suffix == ".pdf" or content_type == "application/pdf":
            sources = parse_pdf_bytes(data, file_name, context)
        elif suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"} or (
            content_type.startswith("image/")
        ):
            sources = parse_image_bytes(data, file_name, context)
        elif suffix in {".xls", ".xlsx"} or "spreadsheet" in content_type:
            sources = parse_excel_bytes(data, file_name, context)
        elif (
            suffix in {".doc", ".docx"}
            or "wordprocessingml" in content_type
            or content_type == "application/msword"
        ):
            sources = parse_word_bytes(data, file_name, context)
        else:
            raise DocumentParseError(f"不支持的文件类型: {file_name} ({mime_type or 'unknown'})")

        if parent_source_id:
            for source in sources:
                source.parent_source_id = parent_source_id
        return sources

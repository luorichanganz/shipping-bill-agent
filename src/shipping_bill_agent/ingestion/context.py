from __future__ import annotations

from dataclasses import dataclass, field

from shipping_bill_agent.config import Settings
from shipping_bill_agent.ocr.client import OCRClient
from shipping_bill_agent.ocr.reviewer import VisionOCRReviewer


@dataclass
class ParserContext:
    settings: Settings
    ocr_client: OCRClient | None = None
    ocr_reviewer: VisionOCRReviewer | None = None
    attachment_count: int = 0
    attachment_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    def accept_attachment(self, size: int) -> bool:
        if self.attachment_count >= self.settings.max_attachment_count:
            self.warnings.append("附件数量超过限制，剩余附件已跳过")
            return False
        if self.attachment_bytes + size > self.settings.max_attachment_bytes:
            self.warnings.append("附件总大小超过限制，剩余附件已跳过")
            return False
        self.attachment_count += 1
        self.attachment_bytes += size
        return True

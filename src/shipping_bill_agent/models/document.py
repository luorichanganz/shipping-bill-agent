from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BlockType(StrEnum):
    TEXT = "text"
    TABLE = "table"
    IMAGE_TEXT = "image_text"
    HEADER = "header"
    FOOTER = "footer"


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    block_type: BlockType = BlockType.TEXT
    text: str = ""
    cells: list[list[str | None]] = Field(default_factory=list)
    page_no: int | None = None
    sheet_name: str | None = None
    row_no: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: str
    file_name: str
    mime_type: str | None = None
    sha256: str
    parent_source_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    blocks: list[ContentBlock] = Field(default_factory=list)

    @property
    def text(self) -> str:
        parts: list[str] = []
        for block in self.blocks:
            if block.text:
                parts.append(block.text)
            elif block.cells:
                parts.extend(" | ".join(cell or "" for cell in row) for row in block.cells)
        return "\n".join(parts)


class ParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourceDocument] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

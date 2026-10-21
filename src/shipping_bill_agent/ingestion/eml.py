from __future__ import annotations

import hashlib
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

from shipping_bill_agent.ingestion.context import ParserContext
from shipping_bill_agent.ingestion.html_text import HTMLTextExtractor
from shipping_bill_agent.models.document import BlockType, ContentBlock, SourceDocument

if TYPE_CHECKING:
    from shipping_bill_agent.ingestion.router import DocumentParser


def decode_header_value(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _surface_parts(message: Message) -> list[Message]:
    """Return direct body/attachment parts without descending into forwarded EML."""
    payload = message.get_payload()
    if not isinstance(payload, list):
        return []
    parts: list[Message] = []
    for part in payload:
        if part.get_content_type() == "message/rfc822":
            parts.append(part)
        elif part.is_multipart():
            parts.extend(_surface_parts(part))
        else:
            parts.append(part)
    return parts


def _body_text(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = [message] if not message.is_multipart() else _surface_parts(message)
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if content_type == "text/plain":
            plain.append(str(content))
        else:
            parser = HTMLTextExtractor()
            parser.feed(str(content))
            html.append(parser.text())
    return "\n".join(plain).strip() or "\n".join(html).strip()


def parse_eml_bytes(
    data: bytes,
    file_name: str,
    context: ParserContext,
    parser: DocumentParser,
    parent_source_id: str | None = None,
    depth: int = 0,
) -> list[SourceDocument]:
    message = BytesParser(policy=policy.default).parsebytes(data)
    digest = hashlib.sha256(data).hexdigest()
    source_id = f"eml-{digest[:16]}"
    warnings: list[str] = []
    if depth > context.settings.max_attachment_depth:
        warnings.append("EML 嵌套深度超过限制，未继续解析附件")
        return [
            SourceDocument(
                source_id=source_id,
                source_type="eml",
                file_name=file_name,
                mime_type="message/rfc822",
                sha256=digest,
                parent_source_id=parent_source_id,
                metadata={"warnings": warnings},
                blocks=[],
            )
        ]

    try:
        sent_at = parsedate_to_datetime(message.get("Date")) if message.get("Date") else None
    except (TypeError, ValueError):
        sent_at = None

    body = _body_text(message)
    attachments = [
        part
        for part in _surface_parts(message)
        if part.get_filename()
        or part.get_content_disposition() == "attachment"
        or part.get_content_type() == "message/rfc822"
    ]
    metadata = {
        "subject": decode_header_value(message.get("Subject")),
        "from": decode_header_value(message.get("From")),
        "to": decode_header_value(message.get("To")),
        "cc": decode_header_value(message.get("Cc")),
        "date": sent_at.isoformat() if sent_at else decode_header_value(message.get("Date")),
        "attachment_names": [decode_header_value(item.get_filename()) for item in attachments],
        "warnings": warnings,
    }
    header_text = "\n".join(
        f"{key.title()}: {metadata[key]}"
        for key in ("subject", "from", "to", "cc", "date")
        if metadata.get(key)
    )
    blocks = [
        ContentBlock(block_id=f"{source_id}:headers", block_type=BlockType.HEADER, text=header_text),
        ContentBlock(block_id=f"{source_id}:body", block_type=BlockType.TEXT, text=body),
    ]
    sources = [
        SourceDocument(
            source_id=source_id,
            source_type="eml",
            file_name=file_name,
            mime_type="message/rfc822",
            sha256=digest,
            parent_source_id=parent_source_id,
            metadata=metadata,
            blocks=[block for block in blocks if block.text],
        )
    ]

    for part in attachments:
        payload = part.get_payload(decode=True)
        if payload is None:
            if part.get_content_type() == "message/rfc822":
                nested = part.get_payload()
                nested_message = nested[0] if isinstance(nested, list) and nested else nested
                payload = nested_message.as_bytes() if isinstance(nested_message, Message) else None
            if payload is None:
                context.warnings.append(f"附件 {part.get_filename()} 无法读取")
                continue
        if not context.accept_attachment(len(payload)):
            break
        attachment_name = part.get_filename() or f"attachment-{context.attachment_count}"
        attachment_name = decode_header_value(attachment_name) or "attachment"
        try:
            sources.extend(
                parser.parse_bytes(
                    payload,
                    attachment_name,
                    part.get_content_type(),
                    parent_source_id=source_id,
                    depth=depth + 1,
                )
            )
        except Exception as exc:
            context.warnings.append(f"附件 {attachment_name} 解析失败: {exc}")
    return sources

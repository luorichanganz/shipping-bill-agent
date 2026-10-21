from __future__ import annotations

import hashlib
import math
import time
from typing import Any

import requests

from shipping_bill_agent.models.document import BlockType, ContentBlock
from shipping_bill_agent.ocr.publisher import FilePublisher

_IGNORED_TEXT_KEYS = {
    "createdate",
    "filename",
    "ordertype",
    "message",
    "success",
    "error_code",
    "task_id",
    "request_id",
    "show_msg",
}
_CONFIDENCE_KEYS = {"confidence", "score", "probability", "certainty"}
_DIRECT_TEXT_KEYS = {"text", "content", "words", "value"}
_PAGE_KEYS = {"page", "page_no", "page_num", "page_number", "pageno", "pagenum"}


def _walk_strings(value: Any, key: str | None = None) -> list[str]:
    found: list[str] = []
    if key is not None and key.casefold() in _IGNORED_TEXT_KEYS:
        return found
    if isinstance(value, str):
        if value.strip():
            found.append(value.strip())
    elif isinstance(value, dict):
        for child_key, item in value.items():
            found.extend(_walk_strings(item, child_key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_strings(item, key))
    return found


def _coerce_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence):
        return None
    if 1 < confidence <= 100:
        confidence /= 100
    return min(1.0, max(0.0, confidence))


def _confidence_for(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        if str(key).casefold() in _CONFIDENCE_KEYS:
            confidence = _coerce_confidence(item)
            if confidence is not None:
                return confidence
    return None


def _page_for(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    for key, item in value.items():
        if str(key).casefold() not in _PAGE_KEYS or isinstance(item, bool):
            continue
        try:
            page_no = int(item)
        except (TypeError, ValueError):
            continue
        if page_no > 0:
            return page_no
    return None


def ocr_response_to_blocks(response: Any, source_id: str) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    table_index = 0
    text_index = 0

    def visit(
        value: Any,
        inherited_confidence: float | None = None,
        inherited_page_no: int | None = None,
    ) -> None:
        nonlocal table_index, text_index
        confidence = _confidence_for(value)
        if confidence is None:
            confidence = inherited_confidence
        page_no = _page_for(value) or inherited_page_no
        is_matrix = (
            isinstance(value, list)
            and value
            and all(
                isinstance(row, list) and all(not isinstance(cell, (list, dict)) for cell in row)
                for row in value
            )
        )
        if is_matrix:
            cells = [[str(cell).strip() if cell is not None else None for cell in row] for row in value]
            if any(any(cell for cell in row) for row in cells):
                table_index += 1
                blocks.append(
                    ContentBlock(
                        block_id=f"{source_id}:ocr-table{table_index}",
                        block_type=BlockType.TABLE,
                        cells=cells,
                        page_no=page_no,
                        confidence=confidence,
                    )
                )
            return
        if isinstance(value, dict):
            direct_texts = [
                item.strip()
                for key, item in value.items()
                if str(key).casefold() in _DIRECT_TEXT_KEYS
                and isinstance(item, str)
                and item.strip()
            ]
            for text in direct_texts:
                text_index += 1
                blocks.append(
                    ContentBlock(
                        block_id=f"{source_id}:ocr-text{text_index}",
                        block_type=BlockType.IMAGE_TEXT,
                        text=text,
                        page_no=page_no,
                        confidence=confidence,
                    )
                )
            for key, item in value.items():
                normalized_key = str(key).casefold()
                if normalized_key in _CONFIDENCE_KEYS:
                    continue
                if normalized_key in _DIRECT_TEXT_KEYS and isinstance(item, str):
                    continue
                visit(item, confidence, page_no)
        elif isinstance(value, list):
            for item in value:
                visit(item, confidence, page_no)

    visit(response, _confidence_for(response), _page_for(response))
    if not blocks:
        text_values = _walk_strings(response)
        if text_values:
            for text in text_values:
                text_index += 1
                blocks.append(
                    ContentBlock(
                        block_id=f"{source_id}:ocr-text{text_index}",
                        block_type=BlockType.IMAGE_TEXT,
                        text=text,
                        confidence=_confidence_for(response),
                    )
                )
    return blocks


class OCRClient:
    def __init__(
        self,
        endpoint: str,
        publisher: FilePublisher,
        task: str = "orderChange",
        connect_timeout: float = 10,
        read_timeout: float = 300,
        session: requests.Session | None = None,
        max_retries: int = 2,
    ):
        self.endpoint = endpoint
        self.publisher = publisher
        self.task = task
        self.timeout = (connect_timeout, read_timeout)
        self.session = session or requests.Session()
        self.max_retries = max_retries

    def _post(self, payload: dict[str, Any]):
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(self.endpoint, json=payload, timeout=self.timeout)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(0.5 * (2**attempt))
        assert last_error is not None
        raise last_error

    def recognize_bytes(self, data: bytes, file_name: str, mime_type: str | None) -> list[ContentBlock]:
        file_url = self.publisher.publish(data, file_name, mime_type)
        response = self._post({"file": file_url, "task": self.task, "callBack": "", "taskType": ""})
        payload = response.json()
        source_id = f"ocr-{hashlib.sha256(file_url.encode()).hexdigest()[:12]}"
        return ocr_response_to_blocks(payload, source_id)

    def recognize_url(self, file_url: str, source_id: str = "ocr-url") -> list[ContentBlock]:
        response = self._post({"file": file_url, "task": self.task, "callBack": "", "taskType": ""})
        return ocr_response_to_blocks(response.json(), source_id)

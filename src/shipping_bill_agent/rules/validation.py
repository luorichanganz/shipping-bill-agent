from __future__ import annotations

import re
from collections import Counter, defaultdict
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from shipping_bill_agent.models.bill import ExtractedDocument
from shipping_bill_agent.models.document import BlockType, SourceDocument
from shipping_bill_agent.normalization.values import money_equal, normalize_text

_CONFUSABLE_MONEY_RE = re.compile(
    r"(?<![\w])(?=[0-9OoIl]*[.,][0-9OoIl]{1,2}(?![\w]))"
    r"(?=[0-9OoIl]*[OoIl])[0-9OoIl]+(?:[.,][0-9OoIl]+)(?![\w])"
)
_CURRENCY_NUMBER_RE = re.compile(
    r"(?<!\w)(?:USD|CNY|RMB|EUR|GBP|JPY|[$¥￥€£])\s*([0-9OoIl][0-9OoIl,.]*)",
    flags=re.IGNORECASE,
)
_IDENTIFIER_TOKEN_RE = re.compile(r"(?<![\w/])([A-Z0-9][A-Z0-9/_-]{5,})(?![\w/])", flags=re.IGNORECASE)
_IDENTIFIER_FIELDS = ("reference_no", "bill_of_lading_no", "booking_no", "container_no")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    charge_index: int | None = None
    source_id: str | None = None
    block_id: str | None = None
    quote: str | None = None


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    errors: list[Finding] = Field(default_factory=list)
    warnings: list[Finding] = Field(default_factory=list)

    def has_hard_error(self, charge_index: int | None = None) -> bool:
        return any(
            finding.charge_index is None or finding.charge_index == charge_index for finding in self.errors
        )

    def messages_for(self, charge_index: int | None = None) -> list[str]:
        return [
            finding.message
            for finding in self.errors + self.warnings
            if finding.charge_index is None or finding.charge_index == charge_index
        ]


def _valid_charge_values(charge, index: int) -> list[Finding]:
    errors: list[Finding] = []
    if not charge.raw_charge_item.strip():
        errors.append(Finding(code="missing_charge_item", message="费用名称为空", charge_index=index))
    if charge.amount is None or not charge.amount.is_finite():
        errors.append(Finding(code="invalid_amount", message="费用金额无效", charge_index=index))
    if not charge.currency:
        errors.append(Finding(code="invalid_currency", message="费用币种无效或缺失", charge_index=index))
    if charge.confidence <= 0:
        errors.append(Finding(code="missing_confidence", message="费用行缺少置信度", charge_index=index))
    return errors


def _is_ocr_block(block) -> bool:
    return block.block_type == BlockType.IMAGE_TEXT or ":ocr-" in block.block_id.casefold()


def _confusable_key(value: str) -> str:
    return value.upper().translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def _has_mixed_numeric_ambiguity(token: str) -> bool:
    return any(character.isdigit() for character in token) and any(
        character.upper() in {"O", "I", "L"} for character in token
    )


def _quote_for_token(text: str, token: str) -> str:
    for line in text.splitlines():
        if token in line:
            return line.strip()[:500]
    return text.strip()[:500]


def validate_ocr_confusions(
    document: ExtractedDocument,
    sources: list[SourceDocument],
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()

    def add_finding(source: SourceDocument, block_id: str, token: str, message: str, quote: str) -> None:
        key = (source.source_id, block_id, token)
        if key in seen:
            return
        seen.add(key)
        findings.append(
            Finding(
                code="ocr_confusable_character",
                message=message,
                source_id=source.source_id,
                block_id=block_id,
                quote=quote,
            )
        )

    identifier_values = [
        str(value)
        for field in _IDENTIFIER_FIELDS
        if (value := getattr(document, field, None))
    ]

    for source in sources:
        for block in source.blocks:
            if not _is_ocr_block(block):
                continue
            text = block.text
            if not text and block.cells:
                text = "\n".join(" | ".join(cell or "" for cell in row) for row in block.cells)
            if not text:
                continue

            for match in _CONFUSABLE_MONEY_RE.finditer(text):
                token = match.group(0)
                add_finding(
                    source,
                    block.block_id,
                    token,
                    f"OCR 金额 {token} 含疑似 O/0 或 I/1 混淆字符，不能自动入账",
                    _quote_for_token(text, token),
                )

            for match in _CURRENCY_NUMBER_RE.finditer(text):
                token = match.group(1)
                if _has_mixed_numeric_ambiguity(token):
                    add_finding(
                        source,
                        block.block_id,
                        token,
                        f"OCR 币种金额 {token} 含疑似 O/0 或 I/1 混淆字符，不能自动入账",
                        _quote_for_token(text, match.group(0)),
                    )

            for match in _IDENTIFIER_TOKEN_RE.finditer(text):
                token = match.group(1)
                if not _has_mixed_numeric_ambiguity(token):
                    continue
                for value in identifier_values:
                    if (
                        len(token) == len(value)
                        and token.casefold() != value.casefold()
                        and _confusable_key(token) == _confusable_key(value)
                    ):
                        add_finding(
                            source,
                            block.block_id,
                            token,
                            f"OCR 标识 {token} 与抽取值 {value} 仅存在疑似 O/0 或 I/1 差异，需人工确认",
                            _quote_for_token(text, token),
                        )

    return findings


def _ocr_blocks(source: SourceDocument) -> list:
    return [block for block in source.blocks if _is_ocr_block(block)]


def _block_quote(block) -> str:
    text = block.text
    if not text and block.cells:
        text = "\n".join(" | ".join(cell or "" for cell in row) for row in block.cells)
    return (text or "").strip()[:500]


def min_ocr_confidence(sources: list[SourceDocument]) -> float | None:
    values = [
        block.confidence
        for source in sources
        for block in _ocr_blocks(source)
        if block.confidence is not None
    ]
    return min(values) if values else None


def validate_ocr_confidence(
    source: SourceDocument,
    threshold: float,
) -> tuple[float | None, list[Finding], list[Finding]]:
    blocks = _ocr_blocks(source)
    if not blocks:
        return None, [], []
    scored = [block for block in blocks if block.confidence is not None]
    if not scored:
        return (
            None,
            [],
            [
                Finding(
                    code="ocr_confidence_missing",
                    message=f"{source.file_name} 的 OCR 响应未提供置信度，无法执行置信度门禁",
                    source_id=source.source_id,
                    block_id=blocks[0].block_id,
                    quote=_block_quote(blocks[0]),
                )
            ],
        )

    lowest = min(scored, key=lambda block: block.confidence)
    confidence = lowest.confidence
    errors: list[Finding] = []
    warnings: list[Finding] = []
    if confidence is not None and confidence < threshold:
        errors.append(
            Finding(
                code="low_ocr_confidence",
                message=(
                    f"OCR 置信度 {confidence:.2f} 低于阈值 {threshold:.2f}，"
                    "不能自动入账"
                ),
                source_id=source.source_id,
                block_id=lowest.block_id,
                quote=_block_quote(lowest),
            )
        )
    if len(scored) < len(blocks):
        warnings.append(
            Finding(
                code="ocr_confidence_partial",
                message=f"{source.file_name} 的部分 OCR 内容没有返回置信度",
                source_id=source.source_id,
                block_id=blocks[0].block_id,
                quote=_block_quote(blocks[0]),
            )
        )
    return confidence, errors, warnings


def validate_pdf_ocr_consistency(
    source: SourceDocument,
) -> tuple[list[Finding], list[Finding]]:
    if source.source_type != "pdf":
        return [], []
    embedded_blocks = [block for block in source.blocks if not _is_ocr_block(block)]
    ocr_blocks = _ocr_blocks(source)
    if not embedded_blocks or not ocr_blocks:
        return [], []

    from shipping_bill_agent.extraction.heuristic import HeuristicExtractor

    extractor = HeuristicExtractor()
    embedded_source = source.model_copy(update={"blocks": embedded_blocks, "metadata": {}})
    ocr_source = source.model_copy(update={"blocks": ocr_blocks, "metadata": {}})
    embedded = extractor.extract(embedded_source)
    recognized = extractor.extract(ocr_source)
    evidence_block_id = ocr_blocks[0].block_id

    def comparison_quote(embedded_value, ocr_value) -> str:
        return (
            f"内嵌文本: {embedded_value if embedded_value is not None else '未提取到'}\n"
            f"OCR文本: {ocr_value if ocr_value is not None else '未提取到'}"
        )[:500]

    errors: list[Finding] = []
    warnings: list[Finding] = []
    field_labels = {
        "reference_no": "单据编号",
        "document_date": "单据日期",
        "issuer": "签发方",
        "recipient": "接收方",
        "bill_of_lading_no": "提单号",
        "booking_no": "订舱号",
        "container_no": "箱号",
    }
    for field, label in field_labels.items():
        embedded_value = getattr(embedded, field)
        ocr_value = getattr(recognized, field)
        if embedded_value is None and ocr_value is None:
            continue
        left = embedded_value.isoformat() if hasattr(embedded_value, "isoformat") else embedded_value
        right = ocr_value.isoformat() if hasattr(ocr_value, "isoformat") else ocr_value
        if left is None or right is None:
            warnings.append(
                Finding(
                    code="pdf_ocr_field_missing",
                    message=f"{source.file_name} 的{label}仅在一侧文本中提取到，无法完整交叉确认",
                    source_id=source.source_id,
                    block_id=evidence_block_id,
                    quote=comparison_quote(left, right),
                )
            )
        elif normalize_text(str(left)) != normalize_text(str(right)):
            errors.append(
                Finding(
                    code="pdf_ocr_field_mismatch",
                    message=f"{source.file_name} 的{label}在内嵌文本与 OCR 文本中不一致",
                    source_id=source.source_id,
                    block_id=evidence_block_id,
                    quote=comparison_quote(left, right),
                )
            )

    amount_labels = {"subtotal": "小计", "tax": "税额", "total": "合计"}
    for field, label in amount_labels.items():
        embedded_value = getattr(embedded, field)
        ocr_value = getattr(recognized, field)
        if embedded_value is None and ocr_value is None:
            continue
        if embedded_value is None or ocr_value is None:
            warnings.append(
                Finding(
                    code="pdf_ocr_field_missing",
                    message=f"{source.file_name} 的{label}仅在一侧文本中提取到，无法完整交叉确认",
                    source_id=source.source_id,
                    block_id=evidence_block_id,
                    quote=comparison_quote(embedded_value, ocr_value),
                )
            )
        elif not money_equal(embedded_value, ocr_value):
            errors.append(
                Finding(
                    code="pdf_ocr_field_mismatch",
                    message=f"{source.file_name} 的{label}在内嵌文本与 OCR 文本中不一致",
                    source_id=source.source_id,
                    block_id=evidence_block_id,
                    quote=comparison_quote(embedded_value, ocr_value),
                )
            )

    def charge_signatures(document) -> Counter:
        return Counter(
            (
                normalize_text(charge.raw_charge_item),
                charge.amount,
                charge.currency,
            )
            for charge in document.charges
        )

    embedded_charges = charge_signatures(embedded)
    ocr_charges = charge_signatures(recognized)
    if embedded_charges != ocr_charges:
        errors.append(
            Finding(
                code="pdf_ocr_field_mismatch",
                message=f"{source.file_name} 的费用行在内嵌文本与 OCR 文本中不一致",
                source_id=source.source_id,
                block_id=evidence_block_id,
                quote=comparison_quote(embedded_charges, ocr_charges),
            )
        )

    return errors, warnings


def validate_document(document: ExtractedDocument) -> ValidationReport:
    report = ValidationReport()
    for conflict in document.conflicts:
        report.errors.append(
            Finding(
                code="cross_source_conflict",
                message=conflict.message,
                source_id=conflict.source_ids[0] if conflict.source_ids else None,
                block_id=f"cross-source:{conflict.field}",
                quote=" / ".join(conflict.values),
            )
        )
    if not document.charges:
        report.errors.append(Finding(code="no_charges", message="未提取到有效费用项目"))
        return report

    for index, charge in enumerate(document.charges):
        report.errors.extend(_valid_charge_values(charge, index))
        if charge.payment_term not in {None, "PP", "CC"}:
            report.warnings.append(
                Finding(
                    code="non_standard_payment_term",
                    message=f"付款方式 {charge.payment_term} 不在 PP/CC 标准枚举中，保留原值",
                    charge_index=index,
                )
            )

    currencies = {charge.currency for charge in document.charges if charge.currency}
    if len(currencies) > 1:
        report.warnings.append(
            Finding(code="mixed_currencies", message="同一单据包含多个币种，无法直接核对合计")
        )
    else:
        comparison_total = document.subtotal
        comparison_label = "subtotal"
        if comparison_total is None and document.tax is None:
            comparison_total = document.total
            comparison_label = "total"
        if comparison_total is not None and len(currencies) == 1:
            computed = sum(
                (charge.amount or Decimal("0") for charge in document.charges),
                start=Decimal("0"),
            )
            if not money_equal(computed, comparison_total):
                report.errors.append(
                    Finding(
                        code=f"{comparison_label}_mismatch",
                        message=(f"费用明细合计 {computed} 与 {comparison_label} {comparison_total} 不一致"),
                    )
                )

    if document.subtotal is not None and document.tax is not None and document.total is not None:
        expected_total = document.subtotal + document.tax
        if not money_equal(expected_total, document.total):
            report.errors.append(
                Finding(
                    code="total_mismatch",
                    message=f"subtotal + tax = {expected_total}，与 total {document.total} 不一致",
                )
            )

    duplicate_groups: dict[tuple[str, str, Decimal | None], list[int]] = defaultdict(list)
    for index, charge in enumerate(document.charges):
        key = (charge.raw_charge_item.casefold().strip(), charge.currency or "", charge.amount)
        duplicate_groups[key].append(index)
    for indices in duplicate_groups.values():
        if len(indices) > 1:
            for index in indices:
                report.errors.append(
                    Finding(
                        code="duplicate_charge",
                        message="检测到重复费用行",
                        charge_index=index,
                    )
                )

    return report

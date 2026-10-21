from __future__ import annotations

import re
from decimal import Decimal

from shipping_bill_agent.models.bill import ChargeDirection, ExtractedCharge, ExtractedDocument
from shipping_bill_agent.models.document import BlockType, ContentBlock, SourceDocument
from shipping_bill_agent.normalization.values import (
    normalize_currency,
    normalize_date,
    normalize_payment_term,
    parse_decimal,
)

_CHARGE_HEADERS = {"charge item", "charge", "description", "fee", "item", "费用", "费用名称"}
_AMOUNT_HEADERS = {"amount", "total", "amount usd", "金额", "费用金额"}
_CURRENCY_HEADERS = {"currency", "ccy", "币种"}
_TERM_HEADERS = {"payment term", "payment", "term", "付款方式"}
_QUANTITY_HEADERS = {"quantity", "qty", "数量"}
_UNIT_PRICE_HEADERS = {"unit price", "price", "单价"}


def _normalize_header(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _extract_value(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip(" \t:：|")
            if value:
                return value
    return None


def _infer_charge_direction(
    document_type: str | None,
    amount: Decimal | None,
    context: str,
) -> ChargeDirection | None:
    upper_context = context.upper()
    has_credit_marker = bool(
        re.search(
            r"\b(?:CREDIT(?:\s+NOTE|\s+MEMO)?|REFUND|REVERSAL|REBATE|ALLOWANCE)\b",
            upper_context,
        )
    )
    has_charge_marker = bool(
        re.search(
            r"\b(?:CHARGE(?:\s+NOTE)?|DEBIT(?:\s+NOTE|\s+MEMO)?|AMOUNT\s+DUE)\b",
            upper_context,
        )
    )
    if amount is not None and amount < 0:
        return ChargeDirection.CREDIT
    if has_credit_marker and not has_charge_marker:
        return ChargeDirection.CREDIT
    if has_charge_marker and not has_credit_marker:
        return ChargeDirection.CHARGE
    if document_type == "Credit Note":
        return ChargeDirection.CREDIT
    if document_type in {"Debit Note", "Invoice", "Commercial Invoice", "Arrival Notice", "Pre-Alert"}:
        return ChargeDirection.CHARGE
    if has_credit_marker:
        return ChargeDirection.CREDIT
    if has_charge_marker or amount is not None:
        return ChargeDirection.CHARGE
    return None


class HeuristicExtractor:
    """Deterministic extractor used for offline demos, tests and regression fixtures."""

    def extract(self, source: SourceDocument) -> ExtractedDocument:
        text = source.text
        combined = f"{source.file_name}\n{text}"
        document_type = self._document_type(combined)
        reference_no = _extract_value(
            [
                r"^\s*(?:reference(?:\s+no\.?|\s+number)?|ref(?:erence)?|单据编号)\s*[:：#]\s*([^\n|]+)",
                r"^\s*(?:D/C\s*No\.?|debit\s+invoice|invoice\s+no\.?|invoice\s+number)\s*[:：]?\s*([A-Z0-9][A-Z0-9/_-]+)",
                r"\b((?:DN|CN|INV)[-_/][A-Z0-9][A-Z0-9_-]+)\b",
            ],
            combined,
        )
        date_text = _extract_value(
            [
                r"(?:document\s+date|invoice\s+date|单据日期)\s*[:：]?\s*([0-9][^\n|]*)",
                r"(?:D/C\s+DATE)\s+([0-9][^\n|]*)",
                r"^\s*(?:GENERAL|ARRIVAL)\s+NOTICE\s+([0-9][0-9/.-]+)",
            ],
            combined,
        )
        if date_text is None:
            date_matches = re.findall(
                r"^\s*date\s*[:：]\s*([^\n|]+)",
                combined,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            date_text = date_matches[-1] if date_matches else None
        issuer = _extract_value(
            [
                r"^\s*(?:issuer|issued\s+by|biller|签发方)\s*[:：]\s*([^\n|]+)",
                r"^\s*from\s*[:：]\s*([^\n|]+)",
                r"\b(?:GRAND\s+TOTAL\s+)?BALANCE\s+DUE\s+TO\s+([A-Z][A-Z0-9 &.,()'-]+?)(?=\s+USD\b|\n|$)",
            ],
            combined,
        )
        recipient = _extract_value(
            [
                r"^\s*(?:recipient|bill\s+to|收件方|接收方)\s*[:：]\s*([^\n|]+)",
                r"^\s*to\s*[:：]\s*([^\n|]+)",
                r"^\s*(ALL\s+AMERICA\s+FORWARDING,\s*INC\.)\s*$",
            ],
            combined,
        )
        bill_of_lading_no = _extract_value(
            [
                r"^\s*(?:bill\s+of\s+lading(?:\s+no\.?)?|b/l(?:\s+no\.?)?|MBL|HBL|提单号)\s*(?:NO\.?)?\s*[:：]\s*([^\n|]+)",
                r"^\s*MASTER\s+B/L\s+NO\.?\s*[:：]\s*([^\n|]+)",
                r"^\s*HOUSE\s+B/L\s+NO\.?\s*[:：]\s*([^\n|]+)",
                r"^\s*BL\s*[:：]\s*([A-Z0-9]+)",
                r"(?s)Please\s+clear.*?B/L\s*\n\s*([A-Z0-9]{6,})",
                r"^\s*SHIPPER/EXPORTER\s+DOCUMENT\s+NO\.\s+B/L\s+NUMBER\s*\n.*?\s+([A-Z0-9]{6,})\s*$",
                r"^\s*SHIPPER\s+BOOKING\s+NUMBER\s+B/L\s+NO:\s*\n.*?\s+\d{6,}\s+([A-Z0-9]{6,})\s*$",
            ],
            combined,
        )
        booking_no = _extract_value(
            [
                r"^\s*(?:booking(?:\s+(?:no\.?|number))?|订舱号)\s*[:：]\s*([^\n|]+)",
                r"^\s*SHIPPER\s+BOOKING\s+NUMBER\s+B/L\s+NO:\s*\n.*?\s+([0-9]{6,})\s+[A-Z0-9]{6,}\s*$",
            ],
            combined,
        )
        container_no = _extract_value(
            [
                r"(?:container|CNTR\.?)(?:\s+no\.?)?\s*[:：._-]*\s*([A-Z]{4}\d{7})",
                r"^\s*(?:箱号)\s*[:：]\s*([A-Z]{4}\d{7})",
            ],
            combined,
        )
        if container_no is None:
            text_lines = text.splitlines()
            for index, line in enumerate(text_lines):
                if re.search(
                    r"\b(?:CONTAINER|CNTR|40\s*HC|40\s*HQ|20\s*ST|20\s*GP)\b",
                    line,
                    re.IGNORECASE,
                ):
                    container_match = re.search(r"\b([A-Z]{4}\d{7})\b", line)
                    if container_match is None and index + 1 < len(text_lines):
                        container_match = re.search(r"\b([A-Z]{4}\d{7})\b", text_lines[index + 1])
                    if container_match:
                        container_no = container_match.group(1)
                        break
        subtotal = self._decimal_value(["subtotal", "sub-total", "小计"], combined)
        tax = self._decimal_value(["tax", "vat", "税额"], combined)
        total = self._decimal_value(["grand total", "invoice total", "total", "总计", "合计"], combined)
        if total is None:
            standalone_amounts = re.findall(
                r"^\s*(?:USD|CNY|EUR|GBP|JPY|[$¥￥€£])\s+([-+]?(?:\d[\d,.]*\d|\d))\s*$",
                text,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            if standalone_amounts:
                total = parse_decimal(standalone_amounts[-1])
        default_currency = normalize_currency(
            _extract_value(
                [
                    r"\bTOTAL\s+AMOUNT\b.*?\bCURRENCY\s*[:：]?\s*([A-Z]{3})\b",
                    r"^\s*CURRENCY\s*[:：]\s*([A-Z]{3})\b",
                ],
                combined,
            )
        )
        default_payment_term = normalize_payment_term(
            _extract_value(
                [
                    r"^\s*TERMS\s+([^\n|]+)",
                    r"^\s*Payment\s+Due\s*[:：]\s*([^\n|]+)",
                ],
                combined,
            )
        )

        if issuer is None and document_type == "Arrival Notice":
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), None)
            if first_line and re.search(r"\b(?:SHIPPING|LOGISTICS|LTD|LLC|CORPORATION)\b", first_line, re.I):
                issuer = first_line
        if issuer is None and document_type in {"Invoice", "Commercial Invoice", "Debit Note"}:
            for line in (item.strip() for item in text.splitlines()[:12]):
                if re.search(r"\b(?:SHIPPING|LOGISTICS|LTD|LLC|CORPORATION)\b", line, re.I):
                    issuer = line
                    break
        if issuer is None and document_type == "Bill of Lading":
            received_candidates = re.findall(
                r"\bReceived\s+by\s+([A-Z][A-Z0-9 &.,()'-]+?)\s+in\s+apparent",
                combined,
            )
            issuer = next(
                (
                    candidate
                    for candidate in received_candidates
                    if re.search(r"\b(?:CO|LTD|LLC|SHIPPING)\b", candidate)
                ),
                None,
            )
        if issuer is None and document_type == "Bill of Lading":
            for line in text.splitlines()[:10]:
                if (
                    re.search(r"\b(?:SHIPPING|LOGISTICS|LTD|LLC)\b", line, re.I)
                    and "湃乐多" not in line
                    and "Pilot Aiship" not in line
                ):
                    company_match = re.match(
                        r"^\s*(.*?(?:LTD\.?|LIMITED|LLC))(?:\s+\d|\s*$)",
                        line,
                        flags=re.IGNORECASE,
                    )
                    issuer = company_match.group(1).strip() if company_match else line.strip()
                    break
        if recipient is None and issuer:
            shipper_match = re.search(
                r"^\s*SHIPPER(?:\s+NOTIFY)?\s*\n\s*([^\n]+)",
                text,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            if shipper_match:
                shipper_line = shipper_match.group(1).strip()
                issuer_position = shipper_line.upper().find(issuer.upper())
                if issuer_position > 0:
                    recipient = shipper_line[:issuer_position].strip(" |")
        if recipient is None and issuer and "BALANCE DUE TO" in combined.upper():
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), None)
            if first_line and first_line.upper() != issuer.upper():
                recipient = first_line

        charges: list[ExtractedCharge] = []
        for block in source.blocks:
            if block.block_type == BlockType.TABLE:
                charges.extend(self._charges_from_table(block, document_type))
        charges.extend(
            self._charges_from_text(
                source.blocks,
                default_currency=default_currency,
                default_payment_term=default_payment_term,
                document_type=document_type,
            )
        )

        required_present = sum(item is not None for item in (reference_no, date_text, issuer, recipient))
        charge_confidence = 0.0
        if charges:
            charge_confidence = sum(charge.confidence for charge in charges) / len(charges)
        confidence = min(0.99, 0.55 + required_present * 0.07 + charge_confidence * 0.2)
        if not charges:
            confidence = min(confidence, 0.35)
        issuer_source_refs = self._source_refs_for_value(source.blocks, issuer)
        recipient_source_refs = self._source_refs_for_value(source.blocks, recipient)

        return ExtractedDocument(
            document_type=document_type,
            reference_no=reference_no.strip() if reference_no else None,
            document_date=normalize_date(date_text),
            issuer=issuer.strip() if issuer else None,
            recipient=recipient.strip() if recipient else None,
            issuer_source_refs=issuer_source_refs,
            recipient_source_refs=recipient_source_refs,
            issuer_confidence=0.9 if issuer_source_refs else 0.0,
            recipient_confidence=0.9 if recipient_source_refs else 0.0,
            bill_of_lading_no=bill_of_lading_no.strip() if bill_of_lading_no else None,
            booking_no=booking_no.strip() if booking_no else None,
            container_no=container_no.strip() if container_no else None,
            subtotal=subtotal,
            tax=tax,
            total=total,
            charges=charges,
            source_ids=[source.source_id],
            confidence=confidence,
        )

    @staticmethod
    def _source_refs_for_value(blocks: list[ContentBlock], value: str | None) -> list[str]:
        if not value:
            return []
        needle = re.sub(r"\s+", " ", value).strip().casefold()
        refs: list[str] = []
        for block in blocks:
            text = block.text
            if not text and block.cells:
                text = "\n".join(" | ".join(cell or "" for cell in row) for row in block.cells)
            normalized = re.sub(r"\s+", " ", text or "").strip().casefold()
            if needle in normalized:
                refs.append(block.block_id)
        return refs

    @staticmethod
    def _document_type(text: str) -> str | None:
        for label in (
            "Debit Note",
            "Credit Note",
            "Arrival Notice",
            "Pre-Alert",
            "Bill of Lading",
            "Commercial Invoice",
            "Invoice",
        ):
            if re.search(re.escape(label), text, flags=re.IGNORECASE):
                return label
        if re.search(r"\bGENERAL\s+NOTICE\b", text, flags=re.IGNORECASE):
            return "Arrival Notice"
        return None

    @staticmethod
    def _decimal_value(labels: list[str], text: str) -> Decimal | None:
        for label in labels:
            matches = re.finditer(
                rf"^\s*{re.escape(label)}\s*[:：]?\s*([^\n|]+)",
                text,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            for match in matches:
                value = parse_decimal(match.group(1))
                context = match.group(1).upper()
                if value is not None and not any(
                    word in context for word in ("SAY", "CONTAINER", "ORIGINAL", "THREE")
                ):
                    return value
        return None

    def _charges_from_table(
        self,
        block: ContentBlock,
        document_type: str | None,
    ) -> list[ExtractedCharge]:
        if not block.cells:
            return []
        header_index = None
        columns: dict[str, int] = {}
        for index, row in enumerate(block.cells[:10]):
            normalized = [_normalize_header(cell) for cell in row]
            current: dict[str, int] = {}
            for column, value in enumerate(normalized):
                if value in _CHARGE_HEADERS:
                    current["charge"] = column
                elif value in _AMOUNT_HEADERS:
                    current["amount"] = column
                elif value in _CURRENCY_HEADERS:
                    current["currency"] = column
                elif value in _TERM_HEADERS:
                    current["term"] = column
                elif value in _QUANTITY_HEADERS:
                    current["quantity"] = column
                elif value in _UNIT_PRICE_HEADERS:
                    current["unit_price"] = column
            if "charge" in current and "amount" in current:
                header_index = index
                columns = current
                break
        if header_index is None:
            return []

        charges: list[ExtractedCharge] = []
        header_text = " ".join(cell or "" for cell in block.cells[header_index])
        header_currency_match = re.search(r"\b([A-Z]{3})\b", header_text)

        def cell(row: list[str | None], name: str) -> str | None:
            index = columns.get(name)
            if index is None or index >= len(row):
                return None
            value = row[index]
            return value.strip() if value else None

        for row in block.cells[header_index + 1 :]:
            raw_name = cell(row, "charge")
            if not raw_name or _normalize_header(raw_name) in {"total", "subtotal", "合计", "小计"}:
                continue
            amount = parse_decimal(cell(row, "amount"))
            if amount is None:
                continue
            currency = normalize_currency(cell(row, "currency"))
            if currency is None and header_currency_match:
                currency = normalize_currency(header_currency_match.group(1))
            confidence = 0.95
            if currency is None:
                confidence = 0.65
            row_context = " | ".join(cell or "" for cell in row)
            charges.append(
                ExtractedCharge(
                    raw_charge_item=raw_name,
                    quantity=parse_decimal(cell(row, "quantity")),
                    unit_price=parse_decimal(cell(row, "unit_price")),
                    amount=amount,
                    currency=currency,
                    payment_term=normalize_payment_term(cell(row, "term")),
                    charge_direction=_infer_charge_direction(document_type, amount, row_context),
                    source_refs=[block.block_id],
                    confidence=confidence,
                )
            )
        return charges

    def _charges_from_text(
        self,
        blocks: list[ContentBlock],
        default_currency: str | None = None,
        default_payment_term: str | None = None,
        document_type: str | None = None,
    ) -> list[ExtractedCharge]:
        two_column_pattern = re.compile(
            r"^\s*(?P<name>[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff /&()._-]{1,60}?)"
            r"\s{2,}(?P<amount>[-+]?(?:\d[\d,.]*\d|\d))\s+"
            r"(?P<currency>[A-Z]{3})(?:\s+(?P<term>PP|CC))?\s*$"
        )
        inventory_pattern = re.compile(
            r"^\s*(?P<name>.+?)\s+(?P<unit>-)\s+"
            r"(?P<rate>[-+]?(?:\d[\d,.]*\d|\d))\s+"
            r"(?P<quantity>[-+]?(?:\d[\d,.]*\d|\d))\s+"
            r"(?P<pc>[PC])\s+"
            r"(?P<amount>[-+]?(?:\d[\d,.]*\d|\d))\s*$",
            re.IGNORECASE,
        )
        extended_rate_pattern = re.compile(
            r"^\s*(?P<name>.+?)\s+(?P<base>[A-Z0-9]+)\s+"
            r"(?P<rate>[-+]?(?:\d[\d,.]*\d|\d))\s+"
            r"(?P<currency>[A-Z]{3})\s+"
            r"(?P<quantity>[-+]?(?:\d[\d,.]*\d|\d))\s+"
            r"(?P<roe>\d+(?:\.\d+)?)\s+"
            r"(?P<currency2>[A-Z]{3})\s+"
            r"(?P<amount>[-+]?(?:\d[\d,.]*\d|\d))\s*$",
            re.IGNORECASE,
        )
        currency_amount_pattern = re.compile(
            r"^\s*(?P<name>.+?)\s+(?P<currency>[A-Z]{3})\s+"
            r"(?P<amount>[-+]?(?:\d[\d,.]*\d|\d))(?:\s+(?P<pc>[PC]))?\s*$",
            re.IGNORECASE,
        )
        symbol_amount_pattern = re.compile(
            r"^\s*(?P<name>.+?(?:H/C|FEE|CHARGE|FREIGHT|WHARFAGE|TRUCKING|CUSTOMS|STORAGE).*?)"
            r"\s*[:：]?\s*(?P<currency>[$¥￥€£]|USD|CNY|RMB|EUR|GBP|JPY)\s*"
            r"(?P<amount>[-+]?(?:\d[\d,.]*\d|\d))\s*$",
            re.IGNORECASE,
        )
        skip = {
            "subtotal",
            "sub-total",
            "total",
            "grand total",
            "tax",
            "vat",
            "小计",
            "合计",
            "总计",
        }
        charges: list[ExtractedCharge] = []
        for block in blocks:
            if block.block_type == BlockType.TABLE or not block.text:
                continue
            for line in block.text.splitlines():
                if not line.strip() or self._is_summary_line(line):
                    continue
                charge = None
                match = inventory_pattern.match(line)
                if match:
                    charge = self._text_charge(
                        block,
                        match.group("name"),
                        match.group("amount"),
                        default_currency,
                        self._payment_from_pc(match.group("pc")) or default_payment_term,
                        quantity=match.group("quantity"),
                        unit_price=match.group("rate"),
                        confidence=0.92,
                        document_type=document_type,
                        context=line,
                    )
                if charge is None and (match := extended_rate_pattern.match(line)):
                    charge = self._text_charge(
                        block,
                        match.group("name"),
                        match.group("amount"),
                        match.group("currency2") or match.group("currency"),
                        default_payment_term,
                        quantity=match.group("quantity"),
                        unit_price=match.group("rate"),
                        confidence=0.88,
                        document_type=document_type,
                        context=line,
                    )
                if charge is None and (match := currency_amount_pattern.match(line)):
                    charge = self._text_charge(
                        block,
                        match.group("name"),
                        match.group("amount"),
                        match.group("currency"),
                        self._payment_from_pc(match.group("pc")) or default_payment_term,
                        confidence=0.88,
                        document_type=document_type,
                        context=line,
                    )
                if charge is None and (match := symbol_amount_pattern.match(line)):
                    raw_name = re.split(r"[:：]", match.group("name"))[-1].strip()
                    charge = self._text_charge(
                        block,
                        raw_name,
                        match.group("amount"),
                        match.group("currency"),
                        default_payment_term,
                        confidence=0.8,
                        document_type=document_type,
                        context=line,
                    )
                if charge is None and (match := two_column_pattern.match(line)):
                    charge = self._text_charge(
                        block,
                        match.group("name"),
                        match.group("amount"),
                        match.group("currency"),
                        normalize_payment_term(match.group("term")) or default_payment_term,
                        confidence=0.85,
                        document_type=document_type,
                        context=line,
                    )
                if charge is None:
                    continue
                if charge.raw_charge_item.casefold().strip() in skip:
                    continue
                charges.append(charge)
        return charges

    @staticmethod
    def _is_summary_line(line: str) -> bool:
        return bool(
            re.match(
                r"^\s*(?:subtotal|sub-total|total|grand total|paid amount|tax|vat|小计|合计|总计)\b",
                line,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _payment_from_pc(value: str | None) -> str | None:
        if not value:
            return None
        return {"P": "PP", "C": "CC"}.get(value.upper())

    @staticmethod
    def _text_charge(
        block: ContentBlock,
        name: str,
        amount: str,
        currency: str | None,
        payment_term: str | None,
        quantity: str | None = None,
        unit_price: str | None = None,
        confidence: float = 0.85,
        document_type: str | None = None,
        context: str = "",
    ) -> ExtractedCharge | None:
        parsed_amount = parse_decimal(amount)
        normalized_currency = normalize_currency(currency)
        if parsed_amount is None or normalized_currency is None:
            return None
        cleaned_name = re.sub(r"\s+", " ", name).strip(" :-")
        if cleaned_name.upper().startswith("WAYBILL"):
            cleaned_name = re.sub(r"^WAYBILL\s+\S+\s+", "", cleaned_name, flags=re.IGNORECASE)
        waybill_match = re.match(
            r"^[A-Z]{4}\s+[A-Z0-9]+\s+WAYBILL\s+[A-Z0-9]+\s+(.+)$",
            cleaned_name,
            flags=re.IGNORECASE,
        )
        if waybill_match:
            cleaned_name = waybill_match.group(1).strip()
        return ExtractedCharge(
            raw_charge_item=cleaned_name,
            quantity=parse_decimal(quantity),
            unit_price=parse_decimal(unit_price),
            amount=parsed_amount,
            currency=normalized_currency,
            payment_term=payment_term,
            charge_direction=_infer_charge_direction(document_type, parsed_amount, context),
            source_refs=[block.block_id],
            confidence=confidence,
        )


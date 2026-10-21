from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from shipping_bill_agent.models.bill import BillRecord
from shipping_bill_agent.models.output import Operation, OperationResult, json_safe
from shipping_bill_agent.normalization.values import normalize_date


class JsonLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> list[BillRecord]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Ledger JSON must contain a list of bill rows")
        return [BillRecord.model_validate(item) for item in payload]

    def save(self, bills: list[BillRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(json_safe(bills), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary_name, self.path)
        except Exception:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
            raise

    def apply_results(self, results: list[OperationResult]) -> list[str]:
        bills = self.load()
        by_id = {bill.bill_id: bill for bill in bills}
        applied: list[str] = []

        for result in results:
            if result.operation not in {Operation.CREATE, Operation.UPDATE}:
                continue
            if result.operation == Operation.UPDATE:
                matched_id = result.audit_trail.matched_bill_id
                bill = by_id.get(matched_id or "")
                if bill is None:
                    raise ValueError(f"Update target not found: {matched_id}")
                for field in result.audit_trail.changed_fields:
                    if hasattr(bill, field) and hasattr(result.charge_details, field):
                        value = getattr(result.charge_details, field)
                        if field in {"quantity", "unit_price", "amount"} and value is not None:
                            value = Decimal(str(value))
                        setattr(bill, field, value)
                    elif field in {"document_type", "reference_no"}:
                        setattr(bill, field, getattr(result.document_info, field))
                    elif field in {"bill_of_lading_no", "booking_no", "container_no"}:
                        setattr(bill, field, getattr(result.document_info, field))
                    elif field in {"issuer", "recipient"}:
                        setattr(bill, field, getattr(result.transaction_party, field))
                    elif field == "document_date":
                        bill.document_date = normalize_date(result.document_info.document_date)
                    elif field == "posting_type":
                        bill.posting_type = result.financial_attributes.posting_type
                bill.source_hashes = list(
                    dict.fromkeys(bill.source_hashes + result.audit_trail.source_hashes)
                )
                applied.append(bill.bill_id)
                continue

            bill_id = self._new_bill_id(result)
            if bill_id in by_id:
                continue
            charge = result.charge_details
            bill = BillRecord(
                bill_id=bill_id,
                document_type=result.document_info.document_type,
                reference_no=result.document_info.reference_no,
                document_date=normalize_date(result.document_info.document_date),
                bill_of_lading_no=result.document_info.bill_of_lading_no,
                booking_no=result.document_info.booking_no,
                container_no=result.document_info.container_no,
                issuer=result.transaction_party.issuer,
                recipient=result.transaction_party.recipient,
                raw_charge_item=charge.raw_charge_item or charge.charge_item or "",
                charge_item=charge.charge_item or charge.raw_charge_item or "",
                quantity=charge.quantity,
                unit_price=charge.unit_price,
                amount=charge.amount,
                currency=charge.currency or "",
                payment_term=charge.payment_term,
                charge_direction=charge.charge_direction,
                accounting_entity=result.financial_attributes.accounting_entity or "",
                posting_type=result.financial_attributes.posting_type or "",
                source_hashes=list(dict.fromkeys(result.audit_trail.source_hashes)),
            )
            bills.append(bill)
            by_id[bill_id] = bill
            applied.append(bill_id)

        self.save(bills)
        return applied

    @staticmethod
    def _new_bill_id(result: OperationResult) -> str:
        key = "|".join(
            str(item or "")
            for item in (
                result.document_info.reference_no,
                result.document_info.document_date,
                result.transaction_party.issuer,
                result.transaction_party.recipient,
                result.charge_details.charge_item,
                result.charge_details.raw_charge_item,
                result.charge_details.amount,
                result.charge_details.currency,
                result.charge_details.charge_direction,
            )
        )
        return f"BILL-{hashlib.sha256(key.encode()).hexdigest()[:12].upper()}"

from __future__ import annotations

from typing import Any

from shipping_bill_agent.models.bill import BillRecord, ExtractedCharge, ExtractedDocument
from shipping_bill_agent.normalization.charge_names import ChargeNameNormalizer


def merged_charge_values(
    bill: BillRecord | None,
    document: ExtractedDocument,
    charge: ExtractedCharge,
    charge_normalizer: ChargeNameNormalizer,
) -> dict[str, Any]:
    standard_name = charge_normalizer.normalize(charge.raw_charge_item)[0]
    values = {
        "document_type": document.document_type,
        "reference_no": document.reference_no,
        "document_date": document.document_date,
        "issuer": document.issuer,
        "recipient": document.recipient,
        "bill_of_lading_no": document.bill_of_lading_no,
        "booking_no": document.booking_no,
        "container_no": document.container_no,
        "raw_charge_item": charge.raw_charge_item,
        "charge_item": standard_name,
        "quantity": charge.quantity,
        "unit_price": charge.unit_price,
        "amount": charge.amount,
        "currency": charge.currency,
        "payment_term": charge.payment_term,
        "charge_direction": charge.charge_direction,
    }
    if bill is None:
        return values
    for field, value in list(values.items()):
        if value is None:
            values[field] = getattr(bill, field, None)
    return values


def build_changed_fields(
    bill: BillRecord,
    document: ExtractedDocument,
    charge: ExtractedCharge,
    charge_normalizer: ChargeNameNormalizer,
) -> list[str]:
    proposed = merged_charge_values(None, document, charge, charge_normalizer)
    changed: list[str] = []
    for field, value in proposed.items():
        if value is None:
            continue
        old_value = getattr(bill, field, None)
        if field == "charge_item":
            old_value = charge_normalizer.normalize(str(old_value))[0]
        if old_value != value:
            changed.append(field)
    return changed

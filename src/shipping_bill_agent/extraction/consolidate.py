from __future__ import annotations

from shipping_bill_agent.models.bill import (
    CrossSourceConflict,
    ExtractedCharge,
    ExtractedDocument,
)
from shipping_bill_agent.models.document import SourceDocument
from shipping_bill_agent.normalization.values import money_equal, normalize_party, normalize_text

_FILL_FIELDS = (
    "document_type",
    "reference_no",
    "document_date",
    "issuer",
    "recipient",
    "bill_of_lading_no",
    "booking_no",
    "container_no",
    "subtotal",
    "tax",
    "total",
)

_FIELD_LABELS = {
    "document_type": "单据类型",
    "reference_no": "单据号",
    "document_date": "单据日期",
    "issuer": "签发方",
    "recipient": "接收方",
    "bill_of_lading_no": "提单号",
    "booking_no": "订舱号",
    "container_no": "箱号",
    "subtotal": "小计",
    "tax": "税额",
    "total": "合计",
}
_MONEY_FIELDS = {"subtotal", "tax", "total"}
_PARTY_FIELDS = {"issuer", "recipient"}

_PARTY_EVIDENCE_FIELDS = {
    "issuer": ("issuer_source_refs", "issuer_confidence"),
    "recipient": ("recipient_source_refs", "recipient_confidence"),
}


def _same_bill(left: ExtractedDocument, right: ExtractedDocument) -> bool:
    for field in ("reference_no", "bill_of_lading_no", "booking_no"):
        left_value = getattr(left, field)
        right_value = getattr(right, field)
        if left_value and right_value:
            return normalize_text(str(left_value)) == normalize_text(str(right_value))
    return False


def _values_equal(field: str, left: object, right: object) -> bool:
    if field in _MONEY_FIELDS:
        return money_equal(left, right)
    if field in _PARTY_FIELDS:
        return normalize_party(str(left)) == normalize_party(str(right))
    if field == "document_date":
        return left == right
    return normalize_text(str(left)) == normalize_text(str(right))


def _display_value(value: object) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _source_names(document: ExtractedDocument, by_source: dict[str, SourceDocument]) -> str:
    names = [
        by_source[source_id].file_name
        for source_id in document.source_ids
        if source_id in by_source
    ]
    return "、".join(dict.fromkeys(names)) or "未知来源"


def _field_conflict(
    field: str,
    left: ExtractedDocument,
    right: ExtractedDocument,
    by_source: dict[str, SourceDocument],
) -> CrossSourceConflict:
    left_value = getattr(left, field)
    right_value = getattr(right, field)
    left_name = _source_names(left, by_source)
    right_name = _source_names(right, by_source)
    label = _FIELD_LABELS[field]
    return CrossSourceConflict(
        field=field,
        values=[
            f"{left_name}={_display_value(left_value)}",
            f"{right_name}={_display_value(right_value)}",
        ],
        source_ids=list(dict.fromkeys(left.source_ids + right.source_ids)),
        message=(
            f"不同来源的{label}不一致："
            f"{left_name}={_display_value(left_value)}，"
            f"{right_name}={_display_value(right_value)}"
        ),
    )


def _add_field_conflicts(
    target: ExtractedDocument,
    other: ExtractedDocument,
    by_source: dict[str, SourceDocument],
) -> None:
    if not _same_bill(target, other):
        return
    for field in _FILL_FIELDS:
        left_value = getattr(target, field)
        right_value = getattr(other, field)
        if (
            left_value is not None
            and right_value is not None
            and not _values_equal(field, left_value, right_value)
        ):
            target.conflicts.append(_field_conflict(field, target, other, by_source))


def _charge_groups(
    document: ExtractedDocument,
) -> dict[tuple[str, str], list[ExtractedCharge]]:
    groups: dict[tuple[str, str], list[ExtractedCharge]] = {}
    for charge in document.charges:
        key = (
            normalize_text(charge.raw_charge_item),
            (charge.currency or "").upper(),
        )
        groups.setdefault(key, []).append(charge)
    return groups


def _add_charge_conflicts(
    left: ExtractedDocument,
    right: ExtractedDocument,
    by_source: dict[str, SourceDocument],
) -> None:
    if not _same_bill(left, right):
        return
    left_groups = _charge_groups(left)
    right_groups = _charge_groups(right)
    left_name = _source_names(left, by_source)
    right_name = _source_names(right, by_source)
    for key in left_groups.keys() & right_groups.keys():
        if len(left_groups[key]) != 1 or len(right_groups[key]) != 1:
            continue
        left_charge = left_groups[key][0]
        right_charge = right_groups[key][0]
        if money_equal(left_charge.amount, right_charge.amount):
            continue
        name = left_charge.raw_charge_item
        conflict = CrossSourceConflict(
            field=f"charge:{name}",
            values=[
                f"{left_name}={_display_value(left_charge.amount)}",
                f"{right_name}={_display_value(right_charge.amount)}",
            ],
            source_ids=list(dict.fromkeys(left.source_ids + right.source_ids)),
            message=(
                f"不同来源的费用金额不一致：{name} "
                f"{left_name}={_display_value(left_charge.amount)}，"
                f"{right_name}={_display_value(right_charge.amount)}"
            ),
        )
        left.conflicts.append(conflict)
        right.conflicts.append(conflict.model_copy(deep=True))


def _unique_conflicts(conflicts: list[CrossSourceConflict]) -> list[CrossSourceConflict]:
    unique: dict[tuple[str, tuple[str, ...]], CrossSourceConflict] = {}
    for conflict in conflicts:
        unique[(conflict.field, tuple(conflict.values))] = conflict
    return list(unique.values())


def consolidate_documents(
    sources: list[SourceDocument],
    extracted: list[ExtractedDocument],
) -> list[ExtractedDocument]:
    if not extracted:
        return []
    by_source = {source.source_id: source for source in sources}
    context_documents = [document for document in extracted if not document.charges]
    bill_documents = [document for document in extracted if document.charges]
    if not bill_documents:
        return [max(extracted, key=lambda item: item.confidence)]

    consolidated: list[ExtractedDocument] = []
    seen: set[tuple[object, ...]] = set()
    for document in bill_documents:
        merged = document.model_copy(deep=True)
        for context in context_documents:
            _add_field_conflicts(merged, context, by_source)
            for field in _FILL_FIELDS:
                if getattr(merged, field) is None and getattr(context, field) is not None:
                    setattr(merged, field, getattr(context, field))
                    for evidence_field in _PARTY_EVIDENCE_FIELDS.get(field, ()):
                        setattr(merged, evidence_field, getattr(context, evidence_field))
            for source_id in context.source_ids:
                if source_id not in merged.source_ids:
                    merged.source_ids.append(source_id)
        required_metadata = (
            merged.reference_no,
            merged.document_date,
            merged.issuer,
            merged.recipient,
        )
        average_charge_confidence = (
            sum(charge.confidence for charge in merged.charges) / len(merged.charges)
            if merged.charges
            else 0.0
        )
        merged.confidence = max(
            merged.confidence,
            min(
                0.99,
                0.55
                + sum(value is not None for value in required_metadata) * 0.07
                + average_charge_confidence * 0.2,
            ),
        )
        fingerprint = (
            merged.reference_no,
            merged.issuer,
            merged.recipient,
            tuple(
                (
                    charge.raw_charge_item.casefold().strip(),
                    charge.amount,
                    charge.currency,
                )
                for charge in merged.charges
            ),
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        consolidated.append(merged)

    for index, left in enumerate(consolidated):
        for right in consolidated[index + 1 :]:
            _add_field_conflicts(left, right, by_source)
            _add_field_conflicts(right, left, by_source)
            _add_charge_conflicts(left, right, by_source)

    for document in consolidated:
        document.conflicts = _unique_conflicts(document.conflicts)

    # Keep the source lookup alive for callers that need provenance validation.
    for document in consolidated:
        document.source_ids = [item for item in document.source_ids if item in by_source]
    return consolidated

from __future__ import annotations

from shipping_bill_agent.models.bill import ExtractedDocument
from shipping_bill_agent.normalization.charge_names import ChargeNameNormalizer
from shipping_bill_agent.normalization.values import normalize_currency, normalize_payment_term


def normalize_document(
    document: ExtractedDocument,
    charge_normalizer: ChargeNameNormalizer,
) -> ExtractedDocument:
    normalized = document.model_copy(deep=True)
    for charge in normalized.charges:
        _, name_score, strategy = charge_normalizer.normalize(charge.raw_charge_item)
        charge.currency = normalize_currency(charge.currency)
        charge.payment_term = normalize_payment_term(charge.payment_term)
        if strategy == "fuzzy_alias":
            charge.confidence = min(charge.confidence, name_score)
    return normalized

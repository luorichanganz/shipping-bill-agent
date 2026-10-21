from __future__ import annotations

from shipping_bill_agent.models.bill import ChargeDirection
from shipping_bill_agent.normalization.values import normalize_charge_direction, normalize_party


def determine_posting_type(
    accounting_entity: str | None,
    issuer: str | None,
    recipient: str | None,
    charge_direction: str | ChargeDirection | None,
) -> tuple[str | None, list[str], str]:
    entity = normalize_party(accounting_entity)
    issuer_key = normalize_party(issuer)
    recipient_key = normalize_party(recipient)
    direction = normalize_charge_direction(
        str(charge_direction) if charge_direction is not None else None
    )

    if not entity:
        return None, ["记账主体未提供，无法判断 AR/AP"], "missing_accounting_entity"
    if direction is None:
        return None, ["收费方向缺失或无法识别，不能自动判断 AR/AP"], "missing_charge_direction"
    if entity == issuer_key and entity != recipient_key:
        posting_type = "AR" if direction == "CHARGE" else "AP"
        return posting_type, [], f"记账主体是单据签发方，收费方向为 {direction}"
    if entity == recipient_key and entity != issuer_key:
        posting_type = "AP" if direction == "CHARGE" else "AR"
        return posting_type, [], f"记账主体是单据接收方，收费方向为 {direction}"
    if entity in {issuer_key, recipient_key}:
        return None, ["签发方和接收方归一化后相同，无法判断应收应付"], "ambiguous_parties"
    return None, ["记账主体不属于单据交易双方，无法判断 AR/AP"], "entity_not_in_parties"

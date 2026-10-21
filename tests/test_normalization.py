from decimal import Decimal

from shipping_bill_agent.normalization.charge_names import ChargeNameNormalizer
from shipping_bill_agent.normalization.values import (
    normalize_currency,
    normalize_date,
    normalize_payment_term,
    parse_decimal,
)


def test_money_and_currency_normalization() -> None:
    assert parse_decimal("USD 1,234.50") == Decimal("1234.50")
    assert parse_decimal("1.234,50") == Decimal("1234.50")
    assert parse_decimal("NaN") is None
    assert normalize_currency("usd") == "USD"
    assert normalize_currency("ZZZ") is None
    assert normalize_payment_term("Freight Prepaid") == "PP"
    assert normalize_payment_term("Collect") == "CC"


def test_date_and_charge_alias_normalization() -> None:
    assert normalize_date("20/09/2026").isoformat() == "2026-09-20"
    normalizer = ChargeNameNormalizer()
    assert normalizer.normalize("H/D Fee")[:2] == ("Handling Charge", 1.0)
    assert normalizer.normalize("THC")[0] == "Terminal Handling Charge"

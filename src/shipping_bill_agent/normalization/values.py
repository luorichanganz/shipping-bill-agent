from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pycountry

_SPACE_RE = re.compile(r"\s+")
_MONEY_RE = re.compile(r"[-+]?(?:\d[\d,.\s]*\d|\d)")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    value = re.sub(r"[^\w\s/&.-]", " ", value)
    return _SPACE_RE.sub(" ", value).strip(" .")


def normalize_party(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = unicodedata.normalize("NFKC", value).casefold()
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"\b(?:mr|ms|mrs|dr)\.?\s+", "", cleaned)
    cleaned = re.sub(r"[^\w\s&.-]", " ", cleaned)
    return _SPACE_RE.sub(" ", cleaned).strip(" .-")


def parse_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if Decimal(str(value)).is_finite() else None

    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = _MONEY_RE.search(text)
    if not match:
        return None
    text = re.sub(r"\s+", "", match.group(0))

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        groups = text.replace("-", "").replace("+", "").split(",")
        if len(groups) == 2 and len(groups[1]) in {1, 2}:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")

    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    candidate = unicodedata.normalize("NFKC", value).strip().upper()
    symbol_map = {
        "$": "USD",
        "US$": "USD",
        "RMB": "CNY",
        "￥": "CNY",
        "¥": "JPY",
        "€": "EUR",
        "£": "GBP",
    }
    candidate = symbol_map.get(candidate, candidate)
    if len(candidate) != 3 or not candidate.isalpha():
        return None
    return candidate if pycountry.currencies.get(alpha_3=candidate) else None


def normalize_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = unicodedata.normalize("NFKC", str(value)).strip()
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y%m%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_payment_term(value: str | None) -> str | None:
    if not value:
        return None
    candidate = normalize_text(value)
    if candidate in {"pp", "prepaid", "freight prepaid", "prepaid freight"}:
        return "PP"
    if candidate in {"cc", "collect", "freight collect", "collect freight"}:
        return "CC"
    return value.strip().upper() or None


def normalize_charge_direction(value: str | None) -> str | None:
    if not value:
        return None
    candidate = normalize_text(value)
    if candidate in {"charge", "debit", "debit charge"}:
        return "CHARGE"
    if candidate in {"credit", "credit charge", "refund", "reversal", "rebate", "allowance"}:
        return "CREDIT"
    return None


def money_equal(left: Decimal | None, right: Decimal | None, tolerance: Decimal = Decimal("0.01")) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= tolerance

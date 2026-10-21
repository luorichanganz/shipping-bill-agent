from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _parse_decimal(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().replace(",", "")
    return value


NullableDecimal = Annotated[Decimal | None, BeforeValidator(_parse_decimal)]


class ChargeDirection(StrEnum):
    CHARGE = "CHARGE"
    CREDIT = "CREDIT"


class ExtractedCharge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_charge_item: str
    quantity: NullableDecimal = None
    unit_price: NullableDecimal = None
    amount: NullableDecimal = None
    currency: str | None = None
    payment_term: str | None = None
    charge_direction: ChargeDirection | None = None
    source_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class CrossSourceConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    values: list[str]
    source_ids: list[str]
    message: str


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str | None = None
    reference_no: str | None = None
    document_date: date | None = None
    issuer: str | None = None
    recipient: str | None = None
    issuer_source_refs: list[str] = Field(default_factory=list)
    recipient_source_refs: list[str] = Field(default_factory=list)
    issuer_confidence: float | None = Field(default=None, ge=0, le=1)
    recipient_confidence: float | None = Field(default=None, ge=0, le=1)
    bill_of_lading_no: str | None = None
    booking_no: str | None = None
    container_no: str | None = None
    subtotal: NullableDecimal = None
    tax: NullableDecimal = None
    total: NullableDecimal = None
    charges: list[ExtractedCharge] = Field(default_factory=list)
    conflicts: list[CrossSourceConflict] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class BillRecord(BaseModel):
    """One ledger row. A bill with several charges has one row per charge."""

    model_config = ConfigDict(extra="allow")

    bill_id: str
    document_type: str | None = None
    reference_no: str | None = None
    document_date: date | None = None
    issuer: str | None = None
    recipient: str | None = None
    bill_of_lading_no: str | None = None
    booking_no: str | None = None
    container_no: str | None = None
    raw_charge_item: str
    charge_item: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal
    currency: str
    payment_term: str | None = None
    charge_direction: ChargeDirection | None = None
    accounting_entity: str
    posting_type: str
    source_hashes: list[str] = Field(default_factory=list)


class MatchInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_index: int
    charge_index: int
    match_strategy: str | None = None
    candidate_count: int = 0
    candidate: BillRecord | None = None
    reason: str

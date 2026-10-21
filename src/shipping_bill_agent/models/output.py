from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shipping_bill_agent.models.bill import ChargeDirection


class Operation(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    NO_CHANGE = "NO_CHANGE"
    PENDING_REVIEW = "PENDING_REVIEW"


class BatchStatus(StrEnum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_REVIEW_ITEMS = "COMPLETED_WITH_REVIEW_ITEMS"


class DocumentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    bill_of_lading_no: str | None = None
    booking_no: str | None = None
    container_no: str | None = None


class TransactionParty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str | None = None
    recipient: str | None = None


class ChargeDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_charge_item: str | None = None
    charge_item: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    currency: str | None = None
    payment_term: str | None = None
    charge_direction: ChargeDirection | None = None


class FinancialAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounting_entity: str | None = None
    posting_type: str | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    block_id: str
    quote: str
    field: str | None = None


class AuditTrail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched_bill_id: str | None = None
    match_strategy: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    duplicate_submission: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    source_hashes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_reviews: list[dict[str, Any]] = Field(default_factory=list)
    party_confidence: dict[str, float] = Field(default_factory=dict)
    ai_reasoning: list[str] = Field(default_factory=list)


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Operation
    document_info: DocumentInfo
    transaction_party: TransactionParty
    charge_details: ChargeDetails
    financial_attributes: FinancialAttributes
    audit_trail: AuditTrail

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=False)


class BatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_items: int
    create_count: int
    update_count: int
    no_change_count: int
    pending_review_count: int
    dry_run: bool


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_files: list[str]
    document_type: str | None = None
    reference_no: str | None = None
    raw_charge_item: str | None = None
    charge_item: str | None = None
    amount: float | None = None
    currency: str | None = None
    confidence: float
    reasons: list[str]
    evidence: list[Evidence]
    actions_taken: list[str]
    actions_not_taken: list[str]
    suggested_next_step: str


class BatchNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_files: list[str]
    charge_item: str | None = None
    amount: float | None = None
    currency: str | None = None
    matched_bill_id: str | None = None
    message: str


class BatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BatchStatus
    input_file: str
    summary: BatchSummary
    review_items: list[ReviewItem]
    notices: list[BatchNotice]
    results: list[OperationResult]

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=False)


def json_safe(value: Any) -> Any:
    """Convert Decimal and date values while keeping monetary JSON values numeric."""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, (Decimal, float)):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return json_safe(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value

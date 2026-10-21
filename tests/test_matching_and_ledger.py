from decimal import Decimal

from shipping_bill_agent.matching.matcher import BillMatcher
from shipping_bill_agent.matching.merge import build_changed_fields
from shipping_bill_agent.matching.normalize import normalize_document
from shipping_bill_agent.models.bill import ExtractedCharge, ExtractedDocument
from shipping_bill_agent.normalization.charge_names import ChargeNameNormalizer
from shipping_bill_agent.storage.json_ledger import JsonLedger


def test_merge_only_changes_explicit_fields(sample_bill) -> None:
    document = ExtractedDocument(
        document_type="Debit Note",
        reference_no="DN-202609-001",
        document_date="2026-09-20",
        issuer="Destination Agent",
        recipient="Origin Agent",
        charges=[
            ExtractedCharge(
                raw_charge_item="H/D Fee",
                amount=Decimal("40.00"),
                currency="USD",
                confidence=0.95,
            )
        ],
        confidence=0.95,
    )
    changed = build_changed_fields(
        sample_bill,
        document,
        document.charges[0],
        ChargeNameNormalizer(),
    )
    assert changed == ["amount"]


def test_ledger_update_is_persisted(sample_ledger_path, sample_bill) -> None:
    from shipping_bill_agent.models.output import (
        AuditTrail,
        ChargeDetails,
        DocumentInfo,
        FinancialAttributes,
        Operation,
        OperationResult,
        TransactionParty,
    )

    result = OperationResult(
        operation=Operation.UPDATE,
        document_info=DocumentInfo(
            document_type="Debit Note",
            reference_no="DN-202609-001",
            document_date="2026-09-20",
        ),
        transaction_party=TransactionParty(
            issuer="Destination Agent",
            recipient="Origin Agent",
        ),
        charge_details=ChargeDetails(
            raw_charge_item="H/D Fee",
            charge_item="Handling Charge",
            amount=40.0,
            currency="USD",
            payment_term="PP",
        ),
        financial_attributes=FinancialAttributes(
            accounting_entity="Origin Agent",
            posting_type="AP",
        ),
        audit_trail=AuditTrail(
            matched_bill_id=sample_bill.bill_id,
            changed_fields=["amount"],
            confidence=0.95,
        ),
    )
    ledger = JsonLedger(sample_ledger_path)
    ledger.apply_results([result])
    saved = ledger.load()
    assert saved[0].amount == Decimal("40.0")
    assert saved[0].payment_term == "PP"


def test_multiple_match_candidates_are_not_auto_updated(sample_bill) -> None:
    duplicate = sample_bill.model_copy(update={"bill_id": "BILL-DEMO-002"})
    document = ExtractedDocument(
        document_type="Debit Note",
        reference_no="DN-202609-001",
        document_date="2026-09-20",
        issuer="Destination Agent",
        recipient="Origin Agent",
        charges=[
            ExtractedCharge(
                raw_charge_item="H/D Fee",
                amount=Decimal("40.00"),
                currency="USD",
                confidence=0.95,
            )
        ],
        confidence=0.95,
    )
    normalizer = ChargeNameNormalizer()
    normalized = normalize_document(document, normalizer)
    match = BillMatcher([sample_bill, duplicate], normalizer).match(normalized, normalized.charges[0], 0)
    assert match.candidate_count == 2
    assert match.candidate is None


def test_duplicate_source_hash_matches_existing_charge(sample_bill) -> None:
    bill = sample_bill.model_copy(update={"source_hashes": ["source-hash"]})
    document = ExtractedDocument(
        charges=[
            ExtractedCharge(
                raw_charge_item="H/D Fee",
                amount=Decimal("35.00"),
                currency="USD",
                charge_direction="CHARGE",
                confidence=0.95,
            )
        ]
    )
    normalizer = ChargeNameNormalizer()
    normalized = normalize_document(document, normalizer)

    match = BillMatcher([bill], normalizer).match(
        normalized,
        normalized.charges[0],
        0,
        {"source-hash"},
    )

    assert match.candidate is bill
    assert match.match_strategy == "source_hash+charge"


def test_ledger_persists_source_hash_for_duplicate_detection(tmp_path) -> None:
    from shipping_bill_agent.models.output import (
        AuditTrail,
        ChargeDetails,
        DocumentInfo,
        FinancialAttributes,
        Operation,
        OperationResult,
        TransactionParty,
    )

    result = OperationResult(
        operation=Operation.CREATE,
        document_info=DocumentInfo(
            document_type="Debit Note",
            reference_no="DN-202609-002",
            document_date="2026-09-20",
        ),
        transaction_party=TransactionParty(issuer="Destination Agent", recipient="Origin Agent"),
        charge_details=ChargeDetails(
            raw_charge_item="H/D Fee",
            charge_item="Handling Charge",
            amount=40,
            currency="USD",
            charge_direction="CHARGE",
        ),
        financial_attributes=FinancialAttributes(
            accounting_entity="Origin Agent",
            posting_type="AP",
        ),
        audit_trail=AuditTrail(source_hashes=["source-hash"], confidence=0.95),
    )

    ledger = JsonLedger(tmp_path / "bills.json")
    ledger.apply_results([result])

    assert ledger.load()[0].source_hashes == ["source-hash"]
    assert ledger.load()[0].charge_direction == "CHARGE"

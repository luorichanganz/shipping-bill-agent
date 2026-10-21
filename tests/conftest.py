from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from shipping_bill_agent.models.bill import BillRecord


@pytest.fixture
def sample_bill() -> BillRecord:
    return BillRecord(
        bill_id="BILL-DEMO-001",
        document_type="Debit Note",
        reference_no="DN-202609-001",
        document_date="2026-09-20",
        issuer="Destination Agent",
        recipient="Origin Agent",
        raw_charge_item="H/D Fee",
        charge_item="Handling Charge",
        amount=Decimal("35.00"),
        currency="USD",
        payment_term="PP",
        charge_direction="CHARGE",
        accounting_entity="Origin Agent",
        posting_type="AP",
    )


@pytest.fixture
def sample_ledger_path(tmp_path: Path, sample_bill: BillRecord) -> Iterator[Path]:
    from shipping_bill_agent.storage.json_ledger import JsonLedger

    path = tmp_path / "bills.json"
    JsonLedger(path).save([sample_bill])
    yield path

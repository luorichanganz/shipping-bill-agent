from __future__ import annotations

from collections.abc import Iterable

from shipping_bill_agent.models.bill import BillRecord, ExtractedCharge, ExtractedDocument, MatchInfo
from shipping_bill_agent.normalization.charge_names import ChargeNameNormalizer
from shipping_bill_agent.normalization.values import money_equal, normalize_party


def _same(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.strip().casefold() == right.strip().casefold()


class BillMatcher:
    def __init__(self, bills: Iterable[BillRecord], charge_normalizer: ChargeNameNormalizer):
        self.bills = list(bills)
        self.charge_normalizer = charge_normalizer

    def _charge_name(self, bill: BillRecord) -> str:
        return self.charge_normalizer.normalize(bill.charge_item)[0].casefold().strip()

    def match(
        self,
        document: ExtractedDocument,
        charge: ExtractedCharge,
        document_index: int,
        source_hashes: set[str] | None = None,
    ) -> MatchInfo:
        charge_name = self.charge_normalizer.normalize(charge.raw_charge_item)[0].casefold().strip()
        strategies = [
            (
                "document_reference+charge",
                lambda bill: (
                    _same(document.reference_no, bill.reference_no) and self._charge_name(bill) == charge_name
                ),
            ),
            (
                "bill_of_lading+charge+currency",
                lambda bill: (
                    _same(document.bill_of_lading_no, bill.bill_of_lading_no)
                    and self._charge_name(bill) == charge_name
                    and _same(charge.currency, bill.currency)
                ),
            ),
            (
                "booking+charge+currency",
                lambda bill: (
                    _same(document.booking_no, bill.booking_no)
                    and self._charge_name(bill) == charge_name
                    and _same(charge.currency, bill.currency)
                ),
            ),
            (
                "container+parties+date+amount",
                lambda bill: (
                    _same(document.container_no, bill.container_no)
                    and bool(document.issuer and document.recipient)
                    and normalize_party(document.issuer) == normalize_party(bill.issuer)
                    and normalize_party(document.recipient) == normalize_party(bill.recipient)
                    and document.document_date is not None
                    and document.document_date == bill.document_date
                    and money_equal(charge.amount, bill.amount)
                ),
            ),
            (
                "source_hash+charge",
                lambda bill: (
                    bool(source_hashes)
                    and bool(set(bill.source_hashes).intersection(source_hashes or set()))
                    and self._charge_name(bill) == charge_name
                ),
            ),
        ]

        for name, predicate in strategies:
            candidates = [bill for bill in self.bills if predicate(bill)]
            if not candidates:
                continue
            if len(candidates) == 1:
                return MatchInfo(
                    document_index=document_index,
                    charge_index=0,
                    match_strategy=name,
                    candidate_count=1,
                    candidate=candidates[0],
                    reason=f"通过 {name} 唯一匹配到账单",
                )
            return MatchInfo(
                document_index=document_index,
                charge_index=0,
                match_strategy=name,
                candidate_count=len(candidates),
                candidate=None,
                reason=f"通过 {name} 匹配到 {len(candidates)} 条候选账单，需人工复核",
            )

        return MatchInfo(
            document_index=document_index,
            charge_index=0,
            match_strategy=None,
            candidate_count=0,
            candidate=None,
            reason="未匹配到已有账单",
        )

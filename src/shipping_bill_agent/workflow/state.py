from __future__ import annotations

from typing import TypedDict

from shipping_bill_agent.models.bill import ExtractedDocument, MatchInfo
from shipping_bill_agent.models.document import SourceDocument
from shipping_bill_agent.models.output import OperationResult
from shipping_bill_agent.rules.validation import ValidationReport


class AgentState(TypedDict, total=False):
    input_path: str
    accounting_entity: str | None
    sources: list[SourceDocument]
    parse_warnings: list[str]
    extracted: list[ExtractedDocument]
    documents: list[ExtractedDocument]
    validation: list[ValidationReport]
    matches: list[MatchInfo]
    results: list[OperationResult]
    warnings: list[str]
    fatal_error: str | None

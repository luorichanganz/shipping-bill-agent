from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from shipping_bill_agent.config import Settings
from shipping_bill_agent.extraction.consolidate import consolidate_documents
from shipping_bill_agent.extraction.heuristic import HeuristicExtractor
from shipping_bill_agent.extraction.llm import LLMExtractor
from shipping_bill_agent.ingestion.router import DocumentParser
from shipping_bill_agent.matching.matcher import BillMatcher
from shipping_bill_agent.matching.merge import build_changed_fields, merged_charge_values
from shipping_bill_agent.matching.normalize import normalize_document
from shipping_bill_agent.models.bill import ExtractedCharge, ExtractedDocument, MatchInfo
from shipping_bill_agent.models.document import SourceDocument
from shipping_bill_agent.models.output import (
    AuditTrail,
    ChargeDetails,
    DocumentInfo,
    Evidence,
    FinancialAttributes,
    Operation,
    OperationResult,
    TransactionParty,
)
from shipping_bill_agent.normalization.charge_names import ChargeNameNormalizer
from shipping_bill_agent.normalization.values import normalize_currency, normalize_payment_term
from shipping_bill_agent.rules.posting_type import determine_posting_type
from shipping_bill_agent.rules.validation import (
    Finding,
    ValidationReport,
    min_ocr_confidence,
    validate_document,
    validate_ocr_confidence,
    validate_ocr_confusions,
    validate_pdf_ocr_consistency,
)
from shipping_bill_agent.storage.json_ledger import JsonLedger
from shipping_bill_agent.workflow.state import AgentState


class ShippingBillAgent:
    def __init__(
        self,
        settings: Settings,
        ledger_path: str | Path,
        extractor: str = "heuristic",
        parser: DocumentParser | None = None,
        charge_normalizer: ChargeNameNormalizer | None = None,
    ):
        self.settings = settings
        self.ledger = JsonLedger(ledger_path)
        self.parser = parser or DocumentParser(settings)
        if extractor == "heuristic":
            self.extractor = HeuristicExtractor()
        elif extractor == "llm":
            self.extractor = LLMExtractor(settings)
        else:
            raise ValueError(f"Unknown extractor: {extractor}")
        self.charge_normalizer = charge_normalizer or ChargeNameNormalizer()
        self.graph = self._build_graph()

    def run(self, input_path: str | Path, accounting_entity: str | None) -> list[OperationResult]:
        state: AgentState = {
            "input_path": str(input_path),
            "accounting_entity": accounting_entity,
            "sources": [],
            "parse_warnings": [],
            "extracted": [],
            "documents": [],
            "validation": [],
            "matches": [],
            "results": [],
            "warnings": [],
            "fatal_error": None,
        }
        final_state = self.graph.invoke(state)
        return final_state.get("results", [])

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("parse", self._parse)
        graph.add_node("extract", self._extract)
        graph.add_node("consolidate", self._consolidate)
        graph.add_node("normalize", self._normalize)
        graph.add_node("validate", self._validate)
        graph.add_node("match", self._match)
        graph.add_node("decide", self._decide)
        graph.add_node("review", self._review)
        graph.add_node("fatal", self._fatal_result)

        graph.add_edge(START, "parse")
        graph.add_conditional_edges(
            "parse",
            lambda state: "fatal" if state.get("fatal_error") else "extract",
            {"fatal": "fatal", "extract": "extract"},
        )
        graph.add_edge("extract", "consolidate")
        graph.add_edge("consolidate", "normalize")
        graph.add_edge("normalize", "validate")
        graph.add_edge("validate", "match")
        graph.add_edge("match", "decide")
        graph.add_conditional_edges(
            "decide",
            lambda state: (
                "review"
                if any(result.operation == Operation.PENDING_REVIEW for result in state.get("results", []))
                else END
            ),
            {"review": "review", END: END},
        )
        graph.add_edge("review", END)
        graph.add_edge("fatal", END)
        return graph.compile()

    def _parse(self, state: AgentState) -> dict:
        try:
            result = self.parser.parse_path(state["input_path"])
        except Exception as exc:
            return {"fatal_error": f"文件解析失败: {exc}"}
        return {
            "sources": result.sources,
            "parse_warnings": result.warnings,
            "warnings": list(result.warnings),
        }

    def _extract(self, state: AgentState) -> dict:
        extracted: list[ExtractedDocument] = []
        warnings = list(state.get("warnings", []))
        for source in state.get("sources", []):
            warnings.extend(source.metadata.get("warnings", []))
            if not source.blocks:
                continue
            try:
                extracted.append(self.extractor.extract(source))
            except Exception as exc:
                warnings.append(f"文件 {source.file_name} 抽取失败: {exc}")
        return {"extracted": extracted, "warnings": list(dict.fromkeys(warnings))}

    def _consolidate(self, state: AgentState) -> dict:
        documents = consolidate_documents(state.get("sources", []), state.get("extracted", []))
        return {"documents": documents}

    def _normalize(self, state: AgentState) -> dict:
        documents = [
            normalize_document(document, self.charge_normalizer) for document in state.get("documents", [])
        ]
        return {"documents": documents}

    def _validate(self, state: AgentState) -> dict:
        reports = [validate_document(document) for document in state.get("documents", [])]
        warnings = list(state.get("warnings", []))
        sources = {source.source_id: source for source in state.get("sources", [])}
        for document, report in zip(state.get("documents", []), reports, strict=False):
            related_sources = [
                sources[source_id]
                for source_id in document.source_ids
                if source_id in sources
            ]
            report.errors.extend(validate_ocr_confusions(document, related_sources))
            for source in related_sources:
                if source.source_type == "pdf":
                    consistency_errors, consistency_warnings = validate_pdf_ocr_consistency(source)
                    report.errors.extend(consistency_errors)
                    report.warnings.extend(consistency_warnings)
                _, confidence_errors, confidence_warnings = validate_ocr_confidence(
                    source,
                    self.settings.confidence_threshold,
                )
                report.errors.extend(confidence_errors)
                report.warnings.extend(confidence_warnings)
            for source_id in document.source_ids:
                source = sources.get(source_id)
                if source is None:
                    continue
                if any(
                    str(warning).startswith("OCR 识别失败") for warning in source.metadata.get("warnings", [])
                ):
                    report.errors.append(
                        Finding(
                            code="ocr_failed",
                            message=f"{source.file_name} 的 OCR 调用失败，不能自动入账",
                        )
                    )
            if document.confidence < self.settings.confidence_threshold:
                report.warnings.append(
                    Finding(
                        code="low_document_confidence",
                        message=f"单据整体置信度 {document.confidence:.2f} 低于阈值",
                        charge_index=None,
                    )
                )
            for role, value, refs, confidence in (
                (
                    "签发方",
                    document.issuer,
                    document.issuer_source_refs,
                    document.issuer_confidence,
                ),
                (
                    "接收方",
                    document.recipient,
                    document.recipient_source_refs,
                    document.recipient_confidence,
                ),
            ):
                if not value:
                    continue
                if not refs:
                    report.errors.append(
                        Finding(
                            code=f"missing_{role}_evidence",
                            message=f"{role} {value} 缺少证据块，不能自动判断 AR/AP",
                        )
                    )
                if confidence is None or confidence < self.settings.confidence_threshold:
                    shown = "缺失" if confidence is None else f"{confidence:.2f}"
                    report.errors.append(
                        Finding(
                            code=f"low_{role}_confidence",
                            message=(
                                f"{role}识别置信度 {shown} 低于阈值 "
                                f"{self.settings.confidence_threshold:.2f}，不能自动判断 AR/AP"
                            ),
                        )
                    )
            warnings.extend(finding.message for finding in report.errors + report.warnings)
        return {"validation": reports, "warnings": list(dict.fromkeys(warnings))}

    def _match(self, state: AgentState) -> dict:
        matcher = BillMatcher(self.ledger.load(), self.charge_normalizer)
        matches: list[MatchInfo] = []
        sources = {source.source_id: source for source in state.get("sources", [])}
        for document_index, document in enumerate(state.get("documents", [])):
            source_hashes = {
                sources[source_id].sha256
                for source_id in document.source_ids
                if source_id in sources
            }
            for charge_index, charge in enumerate(document.charges):
                match = matcher.match(document, charge, document_index, source_hashes)
                match.charge_index = charge_index
                matches.append(match)
        return {"matches": matches}

    def _decide(self, state: AgentState) -> dict:
        results: list[OperationResult] = []
        sources = {source.source_id: source for source in state.get("sources", [])}
        warnings = state.get("warnings", [])
        documents = state.get("documents", [])
        reports = state.get("validation", [])
        matches = state.get("matches", [])
        match_lookup = {(match.document_index, match.charge_index): match for match in matches}

        for document_index, document in enumerate(documents):
            report = reports[document_index] if document_index < len(reports) else ValidationReport()
            for charge_index, charge in enumerate(document.charges):
                match = match_lookup.get(
                    (document_index, charge_index),
                    MatchInfo(
                        document_index=document_index,
                        charge_index=charge_index,
                        reason="未执行账单匹配",
                    ),
                )
                results.append(
                    self._build_result(
                        state.get("accounting_entity"),
                        document,
                        charge,
                        charge_index,
                        report,
                        match,
                        sources,
                        warnings,
                    )
                )
        if not results:
            result = self._empty_pending_result(
                state.get("accounting_entity"),
                documents,
                sources,
                state.get("input_path", ""),
                warnings,
            )
            results.append(result)
        return {"results": results}

    def _build_result(
        self,
        accounting_entity: str | None,
        document: ExtractedDocument,
        charge: ExtractedCharge,
        charge_index: int,
        report: ValidationReport,
        match: MatchInfo,
        sources: dict[str, SourceDocument],
        global_warnings: list[str],
    ) -> OperationResult:
        bill = match.candidate
        values = merged_charge_values(bill, document, charge, self.charge_normalizer)
        changed_fields = build_changed_fields(bill, document, charge, self.charge_normalizer) if bill else []
        source_hashes = [
            source.sha256
            for source_id in document.source_ids
            if (source := sources.get(source_id)) is not None
        ]
        source_files = list(
            dict.fromkeys(
                source.file_name
                for source_id in document.source_ids
                if (source := sources.get(source_id)) is not None
            )
        )
        duplicate_source = bool(
            bill
            and source_hashes
            and set(source_hashes).intersection(bill.source_hashes)
        )
        direction_conflict = bool(
            bill
            and bill.charge_direction is not None
            and values.get("charge_direction") is not None
            and bill.charge_direction != values.get("charge_direction")
        )
        posting_type, posting_warnings, posting_reason = determine_posting_type(
            accounting_entity,
            values.get("issuer"),
            values.get("recipient"),
            values.get("charge_direction"),
        )
        warnings = report.messages_for(charge_index) + posting_warnings
        if duplicate_source and changed_fields:
            warnings.append("同一源文件重复提交，但抽取结果与原账单不一致，需人工复核")
        if direction_conflict:
            warnings.append("收费方向与原账单冲突，不能自动覆盖")
        for source_id in document.source_ids:
            source = sources.get(source_id)
            if source:
                warnings.extend(str(item) for item in source.metadata.get("warnings", []))
        if charge.confidence < self.settings.confidence_threshold:
            warnings.append(f"费用行置信度 {charge.confidence:.2f} 低于阈值")

        pending = report.has_hard_error(charge_index)
        pending = pending or charge.confidence < self.settings.confidence_threshold
        pending = pending or document.confidence < self.settings.confidence_threshold
        pending = pending or match.candidate_count > 1
        pending = pending or posting_type is None
        pending = pending or (duplicate_source and bool(changed_fields))
        pending = pending or direction_conflict

        if pending:
            operation = Operation.PENDING_REVIEW
            reasoning = [match.reason, posting_reason]
        elif bill is None:
            operation = Operation.CREATE
            reasoning = ["未匹配到已有账单", "费用字段通过校验，执行新增"]
        elif changed_fields:
            operation = Operation.UPDATE
            reasoning = [
                match.reason,
                f"输入明确变更字段: {', '.join(changed_fields)}",
                "其他字段继承原账单值",
            ]
        else:
            operation = Operation.NO_CHANGE
            reasoning = [match.reason, "输入与已有账单一致，无需修改"]

        evidence = self._evidence_for_charge(document, charge, sources)
        evidence.extend(self._party_evidence(document, sources))
        evidence_keys = {(item.source_id, item.block_id, item.field or "") for item in evidence}
        for finding in report.errors + report.warnings:
            if (
                finding.code
                not in {
                    "ocr_confusable_character",
                    "low_ocr_confidence",
                    "pdf_ocr_field_mismatch",
                    "pdf_ocr_field_missing",
                    "cross_source_conflict",
                }
                or not finding.source_id
                or not finding.block_id
                or not finding.quote
                or (finding.source_id, finding.block_id, finding.code) in evidence_keys
            ):
                continue
            evidence.append(
                Evidence(
                    source_id=finding.source_id,
                    block_id=finding.block_id,
                    quote=finding.quote,
                    field=finding.code,
                )
            )
            evidence_keys.add((finding.source_id, finding.block_id, finding.code))
        used_warnings = list(dict.fromkeys(warnings + global_warnings))
        confidence = min(document.confidence, charge.confidence)
        document_sources = [
            source
            for source_id in document.source_ids
            if (source := sources.get(source_id)) is not None
        ]
        ocr_confidence = min_ocr_confidence(document_sources)
        ocr_reviews = [
            review
            for source in document_sources
            for review in source.metadata.get("ocr_reviews", [])
        ]
        if bill is None and operation == Operation.CREATE:
            matched_bill_id = None
        else:
            matched_bill_id = bill.bill_id if bill else None

        return OperationResult(
            operation=operation,
            document_info=DocumentInfo(
                document_type=values.get("document_type"),
                reference_no=values.get("reference_no"),
                document_date=(
                    values["document_date"].isoformat()
                    if hasattr(values.get("document_date"), "isoformat")
                    else values.get("document_date")
                ),
                bill_of_lading_no=values.get("bill_of_lading_no"),
                booking_no=values.get("booking_no"),
                container_no=values.get("container_no"),
            ),
            transaction_party=TransactionParty(
                issuer=values.get("issuer"),
                recipient=values.get("recipient"),
            ),
            charge_details=ChargeDetails(
                raw_charge_item=values.get("raw_charge_item"),
                charge_item=values.get("charge_item"),
                quantity=float(values["quantity"]) if values.get("quantity") is not None else None,
                unit_price=float(values["unit_price"]) if values.get("unit_price") is not None else None,
                amount=float(values["amount"]) if values.get("amount") is not None else None,
                currency=normalize_currency(values.get("currency")),
                payment_term=normalize_payment_term(values.get("payment_term")),
                charge_direction=values.get("charge_direction"),
            ),
            financial_attributes=FinancialAttributes(
                accounting_entity=accounting_entity,
                posting_type=posting_type,
            ),
            audit_trail=AuditTrail(
                matched_bill_id=matched_bill_id,
                match_strategy=match.match_strategy,
                changed_fields=changed_fields,
                source_files=source_files,
                duplicate_submission=duplicate_source,
                evidence=evidence,
                source_hashes=list(dict.fromkeys(source_hashes)),
                warnings=used_warnings,
                confidence=confidence,
                ocr_confidence=ocr_confidence,
                ocr_reviews=ocr_reviews,
                party_confidence={
                    role: value
                    for role, value in (
                        ("issuer", document.issuer_confidence),
                        ("recipient", document.recipient_confidence),
                    )
                    if value is not None
                },
                ai_reasoning=reasoning,
            ),
        )

    @staticmethod
    def _evidence_for_charge(
        document: ExtractedDocument,
        charge: ExtractedCharge,
        sources: dict[str, SourceDocument],
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        for source_id in document.source_ids:
            source = sources.get(source_id)
            if source is None:
                continue
            for block in source.blocks:
                if block.block_id not in charge.source_refs:
                    continue
                quote = block.text
                if not quote and block.cells:
                    quote = "\n".join(" | ".join(cell or "" for cell in row) for row in block.cells)
                evidence.append(
                    Evidence(
                        source_id=source.source_id,
                        block_id=block.block_id,
                        quote=quote[:500],
                        field="raw_charge_item",
                    )
                )
        return evidence

    @staticmethod
    def _party_evidence(
        document: ExtractedDocument,
        sources: dict[str, SourceDocument],
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        for field, refs in (
            ("issuer", document.issuer_source_refs),
            ("recipient", document.recipient_source_refs),
        ):
            for source_id in document.source_ids:
                source = sources.get(source_id)
                if source is None:
                    continue
                for block in source.blocks:
                    if block.block_id not in refs:
                        continue
                    quote = block.text
                    if not quote and block.cells:
                        quote = "\n".join(
                            " | ".join(cell or "" for cell in row)
                            for row in block.cells
                        )
                    evidence.append(
                        Evidence(
                            source_id=source.source_id,
                            block_id=block.block_id,
                            quote=quote[:500],
                            field=field,
                        )
                    )
        return evidence

    @staticmethod
    def _review(state: AgentState) -> dict:
        warnings = list(state.get("warnings", []))
        warnings.append("存在待复核结果，未写回账务数据")
        return {"warnings": list(dict.fromkeys(warnings))}

    @staticmethod
    def _fatal_result(state: AgentState) -> dict:
        message = state.get("fatal_error") or "未知处理错误"
        result = OperationResult(
            operation=Operation.PENDING_REVIEW,
            document_info=DocumentInfo(document_type=None, reference_no=None, document_date=None),
            transaction_party=TransactionParty(issuer=None, recipient=None),
            charge_details=ChargeDetails(),
            financial_attributes=FinancialAttributes(
                accounting_entity=state.get("accounting_entity"),
                posting_type=None,
            ),
            audit_trail=AuditTrail(
                source_files=[Path(state.get("input_path", "")).name]
                if state.get("input_path")
                else [],
                evidence=[],
                warnings=[message],
                confidence=0.0,
                ai_reasoning=["输入文件无法完成解析，未生成任何虚构字段"],
            ),
        )
        return {"results": [result]}

    @staticmethod
    def _empty_pending_result(
        accounting_entity: str | None,
        documents: list[ExtractedDocument],
        sources: dict[str, SourceDocument],
        input_path: str,
        warnings: list[str],
    ) -> OperationResult:
        document = documents[0] if documents else ExtractedDocument()
        source_files = list(
            dict.fromkeys(
                source.file_name
                for source_id in document.source_ids
                if (source := sources.get(source_id)) is not None
            )
        )
        if not source_files and input_path:
            source_files = [Path(input_path).name]
        return OperationResult(
            operation=Operation.PENDING_REVIEW,
            document_info=DocumentInfo(
                document_type=document.document_type,
                reference_no=document.reference_no,
                document_date=document.document_date.isoformat() if document.document_date else None,
                bill_of_lading_no=document.bill_of_lading_no,
                booking_no=document.booking_no,
                container_no=document.container_no,
            ),
            transaction_party=TransactionParty(
                issuer=document.issuer,
                recipient=document.recipient,
            ),
            charge_details=ChargeDetails(),
            financial_attributes=FinancialAttributes(
                accounting_entity=accounting_entity,
                posting_type=None,
            ),
            audit_trail=AuditTrail(
                source_files=source_files,
                evidence=[],
                warnings=list(
                    dict.fromkeys(
                        warnings + ["未提取到有效费用项目，未写回账务数据"]
                    )
                ),
                confidence=document.confidence,
                ai_reasoning=["文档可能无费用、OCR 失败或内容不支持，需要人工复核"],
            ),
        )

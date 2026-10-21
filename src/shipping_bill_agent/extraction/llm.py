from __future__ import annotations

import json

from shipping_bill_agent.config import Settings
from shipping_bill_agent.models.bill import ExtractedDocument
from shipping_bill_agent.models.document import SourceDocument

PROMPT = """你是航运账单抽取器。只根据提供的文档块提取信息，不得推测或补造。

规则：
1. 每个费用项目单独输出，amount 必须是输入中的费用金额。
2. 无法确定的字段使用 null。
3. source_refs 只能填写输入中出现过的 block_id。
4. 保留原始费用名称，不要在此阶段做标准名称映射。
5. confidence 表示本单据抽取整体可信度，范围 0 到 1。
6. charge_direction 只能根据材料中的收费、借记、贷记、退款或冲销语义填写
   CHARGE、CREDIT；无法确定时使用 null。
7. issuer 是出具单据或单据金额应付给的一方；recipient 是被开票、承担付款责任或
   单据明确指向的一方。不要因为公司名出现在页面顶部就自动认定为 issuer。
8. BILL TO、TO 等标签通常指向 recipient。PAYEE、BALANCE DUE TO、REMIT TO、
   BENEFICIARY 等通常支持 issuer 判断，但收款人、BALANCE DUE TO、银行受益人不一定
   等于抬头主体，必须结合单据类型和上下文判断。
9. 若 Debit Note 或 Invoice 的顶部主体与余额应付对象明确不同，且页面没有相反的角色
   标签，则顶部主体是 recipient，BALANCE DUE TO 或 BANK BENEFICIARY 指向的主体是
   issuer；两者都必须引用各自证据。若同一主体在相邻行分别显示中文和外文名称，
   只保留第一行的完整主体名称，不得把多行名称拼成一个字段。
10. 若页面明确角色标签与上述关系冲突、无法区分或只能依赖常识猜测，不要套用该规则，
    将对应字段设为 null。
11. 当顶部主体唯一，且 BALANCE DUE TO 与 BANK BENEFICIARY 指向同一签发方、没有
    相反证据时，可以为两个角色给出不低于 0.85 的置信度；存在冲突时必须降低置信度。
12. issuer_source_refs 和 recipient_source_refs 只能引用支持对应角色的 block_id；
    issuer_confidence 和 recipient_confidence 分别表示角色判断可信度。字段为 null 时，
    对应 source_refs 必须为空、confidence 必须为 0。

文件: {file_name}
文档块:
{blocks}
"""


class LLMExtractor:
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ValueError("LLM 抽取需要 LLM_API_KEY 或 OPENAI_API_KEY")
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "api_key": settings.openai_api_key,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        model = ChatOpenAI(**kwargs)
        prompt = ChatPromptTemplate.from_template(PROMPT)
        self.chain = prompt | model.with_structured_output(
            ExtractedDocument,
            method="function_calling",
        )

    def extract(self, source: SourceDocument) -> ExtractedDocument:
        blocks = [
            {
                "block_id": block.block_id,
                "type": str(block.block_type),
                "page_no": block.page_no,
                "sheet_name": block.sheet_name,
                "text": block.text,
                "cells": block.cells,
            }
            for block in source.blocks
        ]
        result = self.chain.invoke(
            {"file_name": source.file_name, "blocks": json.dumps(blocks, ensure_ascii=False)}
        )
        if not isinstance(result, ExtractedDocument):
            result = ExtractedDocument.model_validate(result)
        if source.source_id not in result.source_ids:
            result.source_ids.append(source.source_id)
        valid_refs = {block.block_id for block in source.blocks}
        for value_field, refs_field, confidence_field in (
            ("issuer", "issuer_source_refs", "issuer_confidence"),
            ("recipient", "recipient_source_refs", "recipient_confidence"),
        ):
            value = getattr(result, value_field)
            refs = [ref for ref in getattr(result, refs_field) if ref in valid_refs]
            setattr(result, refs_field, refs)
            confidence = getattr(result, confidence_field)
            if not value:
                setattr(result, confidence_field, 0.0)
            elif not refs:
                setattr(result, confidence_field, min(confidence or 0.5, 0.5))
            elif confidence is None:
                setattr(result, confidence_field, 0.5)
        for charge in result.charges:
            charge.source_refs = [ref for ref in charge.source_refs if ref in valid_refs]
            if not charge.source_refs:
                charge.confidence = min(charge.confidence, 0.5)
        return result

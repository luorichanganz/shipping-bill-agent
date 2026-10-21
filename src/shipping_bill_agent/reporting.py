from __future__ import annotations

from pathlib import Path

from shipping_bill_agent.models.output import (
    BatchNotice,
    BatchReport,
    BatchStatus,
    BatchSummary,
    Operation,
    OperationResult,
    ReviewItem,
)

_MATCH_STRATEGY_LABELS = {
    "document_reference+charge": "单据号与费用名称",
    "bill_of_lading+charge+currency": "提单号、费用名称与币种",
    "booking+charge+currency": "订舱号、费用名称与币种",
    "container+parties+date+amount": "箱号、交易双方、日期与金额",
    "source_hash+charge": "同一源文件与费用名称",
}


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _humanize_reason(reason: str) -> str:
    for strategy, label in _MATCH_STRATEGY_LABELS.items():
        reason = reason.replace(f"通过 {strategy} 匹配", f"按{label}匹配")
    return reason


def _next_steps(reasons: list[str]) -> list[str]:
    text = "\n".join(reasons)
    steps: list[str] = []
    if "候选" in text:
        steps.append("确认应更新的账单编号，并核对历史候选记录")
    if any(keyword in text for keyword in ("OCR", "内嵌文本", "混淆字符")):
        steps.append("对照原件核对 OCR 文本，修正识别错误的字段")
    if "不同来源" in text:
        steps.append("核对邮件正文、附件等来源，确认应以哪份单据为准")
    if any(keyword in text for keyword in ("合计", "金额", "币种")):
        steps.append("核对费用金额、币种及单据合计")
    if any(keyword in text for keyword in ("签发方", "接收方", "主体", "交易双方")):
        steps.append("确认签发方、接收方及当前记账主体")
    if "收费方向" in text:
        steps.append("确认该费用项是正常收费还是冲销/贷记")
    if "置信度" in text:
        steps.append("检查原始凭证，必要时上传更清晰的文件")
    if any(keyword in text for keyword in ("未提取到有效费用项目", "文件解析失败")):
        steps.append("确认文件内容完整，并确认其中确实包含可识别的费用项目")
    return _unique(steps) or ["核对原文证据和相关字段，修正后重新提交"]


def _review_item(result: OperationResult, input_file: str) -> ReviewItem:
    audit = result.audit_trail
    charge = result.charge_details
    reasons = _unique(
        [_humanize_reason(reason) for reason in audit.warnings + audit.ai_reasoning]
    )
    if not reasons:
        reasons = ["自动校验未通过，需要人工确认"]

    actions_taken = ["已生成待复核结论并保留审计信息"]
    if charge.raw_charge_item or charge.charge_item:
        actions_taken.insert(0, "已提取费用项目并完成自动校验与历史账单匹配")
    else:
        actions_taken.insert(0, "已解析输入文件并记录无法自动处理的原因")

    return ReviewItem(
        source_files=audit.source_files or [input_file],
        document_type=result.document_info.document_type,
        reference_no=result.document_info.reference_no,
        raw_charge_item=charge.raw_charge_item,
        charge_item=charge.charge_item,
        amount=charge.amount,
        currency=charge.currency,
        confidence=audit.confidence,
        reasons=reasons,
        evidence=audit.evidence,
        actions_taken=actions_taken,
        actions_not_taken=["未新增或更新账单记录", "未写入账单库"],
        suggested_next_step="；".join(_next_steps(reasons)),
    )


def _notice(result: OperationResult, input_file: str) -> BatchNotice:
    charge = result.charge_details
    return BatchNotice(
        source_files=result.audit_trail.source_files or [input_file],
        charge_item=charge.charge_item or charge.raw_charge_item,
        amount=charge.amount,
        currency=charge.currency,
        matched_bill_id=result.audit_trail.matched_bill_id,
        message="同一文件重复提交，内容与账单库一致，本次未重复写入账单库",
    )


def build_batch_report(
    results: list[OperationResult],
    input_path: str | Path,
    *,
    dry_run: bool,
) -> BatchReport:
    input_file = Path(input_path).name
    pending = [result for result in results if result.operation == Operation.PENDING_REVIEW]
    duplicate_no_change = [
        result
        for result in results
        if result.operation == Operation.NO_CHANGE
        and result.audit_trail.duplicate_submission
    ]
    summary = BatchSummary(
        total_items=len(results),
        create_count=sum(result.operation == Operation.CREATE for result in results),
        update_count=sum(result.operation == Operation.UPDATE for result in results),
        no_change_count=sum(result.operation == Operation.NO_CHANGE for result in results),
        pending_review_count=len(pending),
        dry_run=dry_run,
    )
    return BatchReport(
        status=(
            BatchStatus.COMPLETED_WITH_REVIEW_ITEMS
            if pending
            else BatchStatus.COMPLETED
        ),
        input_file=input_file,
        summary=summary,
        review_items=[_review_item(result, input_file) for result in pending],
        notices=[_notice(result, input_file) for result in duplicate_no_change],
        results=results,
    )


def _money(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return ""
    return f"{currency or ''} {amount:,.2f}".strip()


def _shorten(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def render_text_report(report: BatchReport) -> str:
    summary = report.summary
    action_labels = (
        ("可新增", summary.create_count),
        ("可更新", summary.update_count),
        ("无需修改", summary.no_change_count),
    ) if report.summary.dry_run else (
        ("已新增", summary.create_count),
        ("已更新", summary.update_count),
        ("无需修改", summary.no_change_count),
    )
    counts = [f"{count} 项{label}" for label, count in action_labels if count]
    if summary.pending_review_count:
        counts.append(f"{summary.pending_review_count} 项需要人工确认，未写入账单库")

    lines = [
        f"处理完成，状态：{report.status}",
        f"输入文件：{report.input_file}",
        (
            f"共发现 {summary.total_items} 个费用项目："
            + ("，".join(counts) if counts else "没有可处理项目")
        ),
    ]
    if report.summary.dry_run:
        lines.append("运行模式：dry-run，仅预览处理结果，未写入账单库。")

    if report.notices:
        lines.extend(["", "重复提交提示："])
        for notice in report.notices:
            charge = notice.charge_item or "未识别费用项目"
            amount = _money(notice.amount, notice.currency)
            if amount:
                charge = f"{charge}，{amount}"
            matched = f"，对应账单 {notice.matched_bill_id}" if notice.matched_bill_id else ""
            lines.append(
                f"- 文件：{'、'.join(notice.source_files)}；费用：{charge}"
                f"{matched}；{notice.message}"
            )

    if not report.review_items:
        return "\n".join(lines)

    lines.extend(["", "待确认项目："])
    for index, item in enumerate(report.review_items, start=1):
        charge_name = item.charge_item or item.raw_charge_item or "未识别费用项目"
        if item.raw_charge_item and item.raw_charge_item != charge_name:
            charge_name = f"{charge_name}（原文：{item.raw_charge_item}）"
        amount = _money(item.amount, item.currency)
        if amount:
            charge_name = f"{charge_name}，{amount}"

        lines.extend(
            [
                "",
                f"{index}. 文件：{'、'.join(item.source_files)}",
                f"   费用：{charge_name}",
            ]
        )
        if item.document_type or item.reference_no:
            lines.append(
                "   单据："
                + " / ".join(
                    value for value in (item.document_type, item.reference_no) if value
                )
            )
        lines.append("   原因：")
        lines.extend(f"     - {reason}" for reason in item.reasons)
        lines.append("   依据：")
        if item.evidence:
            for evidence in item.evidence:
                field = evidence.field or "原文"
                lines.append(
                    f"     - [{field}] {_shorten(evidence.quote)}"
                    f"（{evidence.source_id}/{evidence.block_id}）"
                )
        else:
            lines.append("     - 未保留可定位的原文证据，请直接核对源文件")
        lines.append(f"   系统已做：{'；'.join(item.actions_taken)}")
        lines.append(f"   系统未做：{'；'.join(item.actions_not_taken)}")
        lines.append(f"   建议：{item.suggested_next_step}")

    return "\n".join(lines)

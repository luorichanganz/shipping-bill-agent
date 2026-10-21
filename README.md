# 航运智能账单助手

一个基于 Python、LangChain 和 LangGraph 的多格式航运账单处理 Agent。系统解析 EML、PDF、图片、Excel 和 Word，提取逐项费用，匹配已有账单，并以字段级增量方式执行新增或更新。

## 功能

- EML 邮件头、正文和附件递归解析
- PDF/图片 OCR API 调用，数字 PDF 同时保留内嵌文本证据
- Excel 工作表和 Word 段落/表格解析
- 费用名称、币种、付款方式和日期标准化
- 按四级业务键匹配已有账单
- `CREATE`、`UPDATE`、`NO_CHANGE`、`PENDING_REVIEW`
- 面向用户输出批次汇总、待确认原因、原文证据、已做/未做事项和下一步建议
- 部分成功返回 `COMPLETED_WITH_REVIEW_ITEMS`，待复核属于正常业务结果
- 相同单据内容重复提交时输出 `NO_CHANGE` 和明确的重复提交提示
- 同一单据的邮件正文、附件等来源出现非空字段冲突时进入 `PENDING_REVIEW`
- 字段级更新，保留未明确修改的账单值
- 结合记账主体、签发方、接收方和收费方向判断 AR/AP
- `0/O`、`1/I` 等 OCR 混淆字符、重复、合计冲突和低置信度检查
- LangGraph 状态编排和可离线 Harness
- JSON 账单库和完整审计信息
- 真实样本回归：PDF 账单、到货通知和 EML 内嵌 MBL/HBL

## 安装

项目使用项目目录内的 `.venv`：

```powershell
uv sync --extra dev
```

依赖安装完成后，测试和运行统一通过 `uv run` 使用 `.venv`。也可以手动激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

未安装 `uv` 时，可以使用标准 `pip`：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 配置

复制 `.env.example` 为 `.env` 并填写：

```text
OCR_ENDPOINT=http://36.103.199.11:5002/process
OCR_PUBLIC_DIR=./tmp/ocr-public
OCR_PUBLIC_BASE_URL=http://127.0.0.1:8000
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
VISION_API_KEY=
VISION_BASE_URL=
VISION_MODEL=
```

LLM 通过 OpenAI 兼容接口接入；`.env` 中的配置会被 CLI 自动加载。OCR 服务接收 URL。`OCR_PUBLIC_DIR` 中的文件必须能通过 `OCR_PUBLIC_BASE_URL` 访问。未配置 LLM 模型 Key 时，`--extractor auto` 使用确定性抽取器，便于本地演示和回归测试。

配置 `VISION_MODEL` 后，低于 `CONFIDENCE_THRESHOLD` 的 OCR 块会渲染对应 PDF 页或原始图片并调用视觉模型复核。`VISION_API_KEY` 和 `VISION_BASE_URL` 为空时回退到通用 LLM 配置。视觉复核置信度达标才替换原 OCR 块；否则保留原结果并进入复核。如不希望把账单图片发送给视觉模型服务，保持 `VISION_MODEL` 为空即可禁用。

本地调试时可以单独启动文件服务：

```powershell
uv run python -m http.server 8000 --directory tmp/ocr-public
```

生产环境应把本地文件发布到 OCR 服务能够访问的对象存储，并把 `OCR_PUBLIC_BASE_URL` 替换成对应公网或内网地址。OCR 调用有超时和有限重试；失败、返回空内容、置信度低于 `CONFIDENCE_THRESHOLD` 或发现疑似 `0/O`、`1/I` 混淆字符时会保留告警或进入复核，不会自动入账。数字 PDF 会将内嵌文本与 OCR 文本按单据字段和费用行交叉比对，冲突时进入复核。

跨来源一致性只比较能够确认属于同一张单据的来源：优先要求单据号相同，其次提单号或订舱号相同。非空字段或同一费用行金额不一致时，系统保留附件中的主记录值，同时把冲突来源和双方值写入证据并进入复核；缺少共同业务键的来源不会被强行比较，避免把同一邮件中的不同附件误判为冲突。

旧版 `.doc` 在 Windows 上优先通过 Microsoft Word COM 自动另存为 `.docx`；Word 不可用时再尝试 LibreOffice 的 `soffice`/`libreoffice`。两种结构化转换后端都不可用或失败时，系统会调用 `antiword` 提取纯文本。antiword 模式不保留表格行列结构，会在来源元数据中明确标记，并继续执行常规校验和复核门禁。

## 运行

处理一个文件并打印面向用户的处理汇总：

```powershell
uv run shipping-bill-agent process `
  --input data/samples/updated_bill.eml `
  --ledger data/ledgers/bills.json `
  --accounting-entity "Origin Agent" `
  --dry-run
```

不使用真实 OCR 服务：

```powershell
uv run shipping-bill-agent process `
  --input data/samples/updated_bill.eml `
  --ledger data/ledgers/bills.json `
  --accounting-entity "Origin Agent" `
  --extractor heuristic `
  --no-ocr `
  --dry-run
```

移除 `--dry-run` 后，`CREATE` 和 `UPDATE` 会写回账单 JSON；`PENDING_REVIEW` 和 `NO_CHANGE` 不会改动账单库。

默认输出中文可读汇总。需要程序化调用时使用 `--format json`，输出包含 `status`、`summary`、`review_items` 和完整 `results` 的结构化报告；`--output` 同样保存该结构化报告。

`PENDING_REVIEW` 是正常业务结果，不会以进程异常结束。只要批次中存在待确认项目，最终状态就是 `COMPLETED_WITH_REVIEW_ITEMS`；汇总会明确列出文件、费用、原因、证据、未写库状态和建议的确认动作。

真实样本位于 `笔试题AIAGENT/`，可以直接运行：

```powershell
uv run shipping-bill-agent process `
  --input "笔试题AIAGENT/PDF 示例账单2.pdf" `
  --ledger data/ledgers/bills.json `
  --accounting-entity "湃乐多（上海）航运科技有限公司" `
  --extractor heuristic `
  --no-ocr `
  --dry-run
```

该 PDF 会输出三条记录，分别是两条 `Handling Charge` 和一条 `Detention Charge`。EML 样本会递归解析正文、图片以及 MBL/HBL 两个 PDF 附件。

## 测试

```powershell
uv run pytest
```

运行轻量评估 Harness：

```powershell
uv run python scripts/run_eval.py
```

运行真实 PDF 的在线 LLM 主体角色回归：

```powershell
$env:RUN_LIVE_LLM_TESTS=1
uv run pytest tests/test_real_samples.py::test_real_debit_note_llm_party_roles_and_posting_type
```

当前 Harness 同时验证：

- 示例 EML 的 35 → 40 USD 更新及重复提交后的 `NO_CHANGE`
- 收费方向抽取、AR/AP 判断和源文件哈希持久化
- 真实 Debit Note 的三条费用与总计一致性
- 真实 Arrival Notice 的 On Carriage Additional 费用
- 真实 EML 正文费用以及两个 PDF 附件解析

## Harness 工程

当前场景下，Harness 应设计为“可重复、可定位、可量化”的分层评估工程：

1. 用固定样例测试 EML、PDF、图片、Excel 和 Word 的解析结果与 Schema；

2. 把 OCR 和 LLM 全部 Mock、fixture 化或录制回放，分别评估字符识别、字段抽取和语义理解，避免端到端失败后无法归因；

3. 用确定性规则测试 `CREATE`、`UPDATE`、`NO_CHANGE`、`PENDING_REVIEW`，重点验证冲突、重复提交、多候选、合计不平和主体不明时不会错误自动入账；

4. 做从 EML 和附件解析到最终结构化 JSON 及反馈报告的端到端回归。

数据集按文件格式、供应商版式、语言和异常类型分层，每个样例保存原始文件、预期字段、预期费用行、预期 `operation`、预期复核原因和预期证据。指标不能只看整体准确率，而要分别观察字段准确率、`operation` 的 Precision/Recall、复核召回率、更新字段完整率，以及最关键的“不安全自动写入率”，该指标目标必须为 0。

工程上要求同一输入可重复执行：外部 OCR 和 LLM 使用 fixture、Mock 或录制回放，账本、时间和随机数隔离；CI 分为离线必过回归、真实模型定时回归和线上 Canary；每次失败都要输出具体样例、所在层级、期望值和实际值。当前项目已有 12 项轻量检查，覆盖更新、重复提交、真实 PDF 和 EML；下一步应将其升级为数据驱动的指标报告和分层基线。

## 设计说明

LLM 只负责非结构化语义抽取。金额计算、标准化、匹配、AR/AP、字段合并和校验全部由确定性代码完成。收费方向按材料中的借记、贷记、退款或冲销语义判定；无法确定，或者同一源文件重复提交但抽取结果发生变化时，结果会进入复核。任何多候选、合计不一致、OCR 字符混淆、低置信度或主体不明结果同样不会自动入账。

详细架构、模块边界和验收项见 `PLAN.md`。

## 补充思考

1. **Agent 的价值应以真实业务效率衡量，而不是只以样例通过率衡量。** 不同用户的单据结构、费用口径和复核要求差异很大，单一通用评分标准难以覆盖真实场景；因此评估应同时关注字段级准确率、人工处理时长、待复核率、错误入账率和重复提交率，并持续积累代表性业务样本形成回归集。
2. **高质量测试数据本身就是核心门槛。** 航运账单涉及客户、价格等敏感信息，受隐私、保密和采集授权限制，真实样本往往稀缺且难以共享。系统应以获得授权的脱敏真实样本为主，结合边界案例和人工修正结果，建立可审计、可持续扩充的评测集。
3. **OCR 应采用分层校验，而不是直接信任识别结果。** 当 OCR 置信度低或存在字符混淆时，可以调用视觉语言模型对原始页面复核；但视觉模型结果仍需经过字段证据和确定性规则校验，只有达到门禁的修正确认才可替代原 OCR 内容，否则进入 `PENDING_REVIEW`。
4. **幂等性应按业务键设计。** 源文件哈希随账单持久化，费用记录使用稳定业务键生成 `bill_id`；重复运行应得到 `NO_CHANGE`，而不是新增多条账单，同一文件前后抽取不一致时进入复核。
5. **每个关键结果都需要证据。** 最终 JSON 保存原始费用名称、来源文件、文本块和字段处理理由，便于财务复核而不是只展示模型结论。
6. **OCR 和 LLM 应分开评估。** OCR 关注字符、表格和列对齐，抽取关注字段与语义；混在一起评估时无法判断错误来自哪一层。
7. **数据安全是业务前提。** 账单包含客户、价格和银行信息，OCR 文件 URL、模型调用、日志保留和临时目录清理都需要部署策略。
8. **默认输出不适合直接记账。** `PENDING_REVIEW`、原始名称、标准化名称和冲突原因应进入财务工作台，由规则负责人处理后再提交账务系统。

## 已知限制

- 图片识别依赖外部 OCR 服务。项目实现了 URL 发布、调用、重试和响应适配；端到端识别要求 OCR 服务能访问 `OCR_PUBLIC_BASE_URL`。
- 旧版 `.doc` 在 Windows 优先使用 Microsoft Word，非 Windows 或服务器环境建议安装 LibreOffice。若两者都不可用，可安装 `antiword` 作为纯文本后备；该模式无法保留表格结构，三者都不可用时会明确返回待复核错误。
- 外部已有账单系统字段尚未提供，当前通过 `BillRecord` 和 JSON 适配器接入。
- 不包含汇率换算、Credit Note 的具体冲销科目映射和真实财务系统提交接口。


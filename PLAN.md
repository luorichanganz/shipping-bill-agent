# 航运智能账单助手实施计划

## 1. 目标

构建一个基于 Python 和 LangChain/LangGraph 的智能账单助手，完成以下闭环：

1. 接收 EML、PDF、图片、Excel、Word 和已有账单 JSON。
2. 递归解析 EML 正文及附件。
3. 通过指定 OCR API 识别 PDF 和图片。
4. 从各类文档中提取单据信息和逐项费用。
5. 标准化费用名称、币种和付款方式。
6. 匹配已有账单并生成按费用项拆分的 `CREATE`、`UPDATE`、`NO_CHANGE` 或 `PENDING_REVIEW` 记录。
7. 以当前记账主体判断 AR/AP。
8. 检测重复、冲突、OCR 异常和低置信度结果。
9. 输出合法 JSON，并为关键字段保留来源和处理依据。
10. 提供可重复运行的 Harness、测试样本和端到端验证命令。

## 2. 核心设计原则

### 2.1 确定性逻辑与语义模型分离

- LLM 只负责非结构化文本、表格语义和费用名称的候选识别。
- 金额计算、日期转换、币种校验、匹配、重复判断、AR/AP 和增量合并由确定性代码完成。
- LLM 不得直接覆盖完整历史账单，只能产生结构化草稿或字段级变更建议。

### 2.2 先统一中间表示，再进入业务流程

所有格式先转换为统一的 `Document IR`，保留文本块、表格、页码或行列位置及置信度。后续抽取、证据绑定和复核共用同一套结构。

### 2.3 证据优先

- 每个关键字段尽可能绑定文件、页码、文本块或表格单元格。
- 原始费用名称、标准化结果、OCR 修正值和模型理由均需保留。
- 不得根据业务常识补造输入中不存在的数据。

### 2.4 默认保守入账

- 唯一、高置信度且通过校验的结果才自动进入 `CREATE` 或 `UPDATE`。
- 多候选、字段冲突、合计不一致、主体不明或低置信度结果统一进入 `PENDING_REVIEW`。
- 更新采用字段级 patch，未明确变更的字段从原账单继承。

### 2.5 最小可用实现优先

首版提供 CLI 和文件型账单仓库，不开发前端、账号体系或真实财务系统连接器。接口保留清晰的边界，便于后续替换为数据库或真实系统 API。

## 3. 技术栈与假设

### 3.1 技术栈

- Python 3.11+
- LangChain：结构化输出、模型调用和提示词管理
- LangGraph：流程编排、条件分支、重试和人工复核状态
- Pydantic v2：输入输出 Schema 和配置校验
- `email`：EML 与 MIME 附件解析
- `pdfplumber`：数字 PDF 辅助解析和版面证据提取
- `openpyxl`：Excel 单元格、工作表和合并单元格读取
- `python-docx`：Word 段落和表格读取
- `Pillow`：图片元数据与格式识别
- `pytest`：单元测试和端到端测试
- `requests`：OCR API 调用
- `json` 或 SQLite：首版历史账单存储；优先使用 JSON，便于检查

### 3.2 当前假设

- 调用入口会提供 `accounting_entity`；若 EML 或配置中能明确解析，也可自动填充。
- 已有账单 JSON 的实际字段结构尚未提供，首版通过适配器映射到内部 `BillRecord`，不将外部结构直接传播到业务逻辑。
- 本地 PDF/图片需要先发布为 OCR 服务可访问的 URL；Harness 使用 Mock OCR，生产环境使用配置的临时文件发布器。
- 本需求不包含汇率换算、真实账务系统写入、信用单冲销方向、审批流 UI 和用户权限管理。

## 4. 总体架构

```text
                        ┌────────────────────┐
输入文件 / 邮件 / JSON →│ Ingestion Router    │
                        └─────────┬──────────┘
                                  ↓
                        ┌────────────────────┐
                        │ Parsers             │
                        │ EML/PDF/Image/Excel │
                        │ Word                │
                        └─────────┬──────────┘
                                  ↓
                        ┌────────────────────┐
                        │ Document IR         │
                        │ blocks/tables/evidence│
                        └─────────┬──────────┘
                                  ↓
                        ┌────────────────────┐
                        │ LLM Extraction      │
                        │ structured output   │
                        └─────────┬──────────┘
                                  ↓
                        ┌────────────────────┐
                        │ Normalize + Validate│
                        └─────────┬──────────┘
                                  ↓
                        ┌────────────────────┐
                        │ Ledger Matching     │
                        │ create/update/no-op │
                        └─────────┬──────────┘
                                  ↓
                        ┌────────────────────┐
                        │ Decision Gate       │
                        │ auto or review      │
                        └─────────┬──────────┘
                                  ↓
                        JSON + Audit Trail
```

LangGraph 节点建议如下：

```text
ingest
  → parse
  → extract
  → normalize
  → validate_document
  → resolve_parties
  → match_ledger
  → build_patch
  → validate_result
  → decide
      ├─ auto_output
      └─ pending_review
```

每个节点输出可序列化的 State，便于测试、重放和定位错误。

## 5. 数据模型

### 5.1 Document IR

```text
SourceDocument
  source_id
  source_type
  file_name
  sha256
  parent_source_id
  metadata

ContentBlock
  block_id
  block_type: text | table | image_text | header | footer
  page_no / sheet_name / row_no
  text
  cells
  bbox
  confidence
```

### 5.2 抽取草稿

```text
ExtractedDocument
  document_type
  reference_no
  document_date
  issuer
  recipient
  bill_of_lading_no
  booking_no
  container_no
  subtotal
  tax
  total
  charges[]
  confidence

ExtractedCharge
  raw_charge_item
  quantity
  unit_price
  amount
  currency
  payment_term
  charge_direction
  source_refs[]
  confidence
```

无法提取的字段使用 `null`，不得用空字符串替代。金额和数量在内部使用 `Decimal`，输出 JSON 时转换成数字。

### 5.3 标准输出

在题目示例基础上补充必要但可选的审计字段：

```json
{
  "operation": "UPDATE",
  "document_info": {
    "document_type": "Debit Note",
    "reference_no": "DN-202609-001",
    "document_date": "2026-09-20"
  },
  "transaction_party": {
    "issuer": "Destination Agent",
    "recipient": "Origin Agent"
  },
  "charge_details": {
    "raw_charge_item": "H/D Fee",
    "charge_item": "Handling Charge",
    "amount": 40.0,
    "currency": "USD",
    "payment_term": "PP",
    "charge_direction": "CHARGE"
  },
  "financial_attributes": {
    "accounting_entity": "Origin Agent",
    "posting_type": "AP"
  },
  "audit_trail": {
    "matched_bill_id": "BILL-001",
    "changed_fields": ["amount"],
    "evidence": [],
    "warnings": [],
    "confidence": 0.96,
    "ai_reasoning": [
      "通过单据编号和费用名称匹配到已有账单",
      "输入明确将金额更新为 USD 40.00",
      "其他字段未被明确要求修改，因此保留原值"
    ]
  }
}
```

如果评审要求严格只使用题目示例字段，可通过配置关闭扩展字段；默认保留扩展字段，以满足原始费用名称、低置信度检测和可追溯性要求。

## 6. 模块划分

当前实现目录（测试为平铺文件，未单独建立 `audit/` 或 `mocks/` 子包）：

```text
.
├── AGENTS.md
├── PLAN.md
├── README.md
├── pyproject.toml
├── .env.example
├── src/
│   └── shipping_bill_agent/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── demo_data.py
│       ├── reporting.py
│       ├── models/
│       │   ├── document.py
│       │   ├── bill.py
│       │   └── output.py
│       ├── ingestion/
│       │   ├── context.py
│       │   ├── router.py
│       │   ├── eml.py
│       │   ├── pdf.py
│       │   ├── image.py
│       │   ├── excel.py
│       │   ├── exceptions.py
│       │   ├── html_text.py
│       │   └── word.py
│       ├── ocr/
│       │   ├── client.py
│       │   ├── publisher.py
│       │   └── reviewer.py
│       ├── extraction/
│       │   ├── consolidate.py
│       │   ├── heuristic.py
│       │   └── llm.py
│       ├── normalization/
│       │   ├── charge_names.py
│       │   └── values.py
│       ├── matching/
│       │   ├── matcher.py
│       │   ├── normalize.py
│       │   └── merge.py
│       ├── rules/
│       │   ├── posting_type.py
│       │   └── validation.py
│       ├── workflow/
│       │   ├── graph.py
│       │   └── state.py
│       ├── storage/
│       │   └── json_ledger.py
│       └── audit trail 位于 models/output.py 的 AuditTrail
├── data/
│   ├── charge_aliases.json
│   ├── samples/
│   └── ledgers/
├── tests/
│   ├── conftest.py
│   └── test_*.py
└── scripts/
    ├── generate_samples.py
    └── run_eval.py
```

## 7. 关键业务规则

### 7.1 EML 与附件

1. 解析主题、发件人、收件人、日期、正文和附件。
2. HTML 正文转换为可保留表格结构的文本。
3. 按附件 MIME 类型路由到对应解析器。
4. 限制递归深度、附件数量、单文件和总大小，记录被跳过的原因。
5. 所有附件保留父邮件 `source_id` 和文件哈希。

### 7.2 OCR

1. PDF 和图片按题目要求调用 OCR API。
2. 数字 PDF 可同时解析嵌入文本，用于校验 OCR 结果和补充位置证据，但不能绕开验收要求的 OCR 调用能力。
3. OCR 客户端设置连接超时、读取超时、有限次数重试和失败分类。
4. 原始 OCR 响应以任务级 trace 保存；输出使用适配后的块和表格结构。

### 7.3 费用标准化

1. 先做精确别名映射，再做规范化后的精确匹配，最后才做阈值内的模糊匹配。
2. 标准名称字典外部化，至少覆盖题目给出的六组映射。
3. 同时保存 `raw_charge_item` 和 `charge_item`。
4. 未确定的模糊匹配不得静默归并。

### 7.4 新增、更新和无变化

| 条件 | 结果 |
|---|---|
| 无历史匹配，费用有效且达到自动处理阈值 | `CREATE` |
| 唯一匹配，输入明确变更至少一个字段 | `UPDATE` |
| 唯一匹配，输入和历史值一致 | `NO_CHANGE` |
| 多候选、主体或字段冲突、合计不平、低置信度 | `PENDING_REVIEW` |

一个单据包含多个费用项时，每个费用项单独决策和输出，不因某一项失败而丢失其他可处理项。

### 7.5 匹配优先级

1. 单据编号 + 标准费用名称
2. 提单号 + 标准费用名称 + 币种
3. 订舱号 + 标准费用名称 + 币种
4. 箱号 + 交易双方 + 日期 + 金额

每级匹配都必须检查候选数量。多候选不自动降低到下一级“随便选一个”，而是保留候选并进入复核。

### 7.6 增量更新

1. 匹配器返回目标 `charge_id`。
2. 比较器生成 `changed_fields` patch。
3. 合并器复制历史记录，仅应用明确变化的字段。
4. 金额、币种、付款方式或交易主体变化时必须重新执行相关校验。
5. 更新后再次校验总计、AR/AP 和费用唯一性。

### 7.7 AR/AP

- `accounting_entity == issuer` 且收费方向为 `CHARGE`：`AR`。
- `accounting_entity == recipient` 且收费方向为 `CHARGE`：`AP`。
- 收费方向为 `CREDIT` 时反转：签发方视角为 `AP`，接收方视角为 `AR`。
- 当前主体不属于交易双方、收费方向缺失或主体无法可靠识别：`PENDING_REVIEW`。
- Debit Note / Credit Note 只作为推断收费方向的辅助信号，不直接映射为 AR/AP。

### 7.8 校验

至少实现：

- 金额为有限有效数字，拒绝 `NaN`、`Infinity` 和非数字字符串。
- 数量、单价和金额关系在字段齐全时校验。
- 明细金额之和与 `subtotal` 比较。
- `subtotal + tax` 与 `total` 比较。
- 币种符合 ISO 4217。
- 日期符合 ISO 8601。
- 付款方式仅接受配置中的业务枚举或进入复核。
- OCR 疑似混淆字符需要上下文支持后才能修正。
- 同一单据内按“标准费用名称 + 币种 + 金额 + 关键业务键”检测重复费用行。

## 8. LangChain/LangGraph 实现方式

1. 使用 Pydantic Schema 约束 LLM 抽取输出。
2. 将 Document IR 转为带 `block_id` 的结构化输入，要求模型引用证据块。
3. 对 Schema 错误最多进行一次带错误信息的修复重试。
4. 抽取结果通过业务校验后，才进入匹配节点。
5. LangGraph 中用条件边处理 `valid`、`retryable`、`pending_review` 三种状态。
6. 固定并记录模型名、温度、提示词版本和别名表版本。
7. 在无模型 API Key 或测试环境中，提供 deterministic fake extractor，保证解析、规则和 Harness 可独立运行。

## 9. Harness 与测试计划

### 9.1 测试分层

| 层级 | 内容 | 验证结果 |
|---|---|---|
| Schema 单元测试 | 日期、金额、币种、付款方式 | 非法输入被拒绝 |
| Parser 单元测试 | 每种格式及 EML 嵌套附件 | 生成稳定 Document IR |
| OCR 契约测试 | 成功、超时、空响应、乱码、表格错位 | 可控重试或复核 |
| Normalizer 测试 | 别名、大小写、全半角、模糊阈值 | 标准名称及原始名称均正确 |
| Matcher 测试 | 四级键、唯一命中、多候选、无命中 | operation 和候选正确 |
| Merge 测试 | 单字段、多字段、冲突及重复输入 | 未修改字段不丢失 |
| AR/AP 测试 | 签发方、接收方、第三方、未知主体 | 正确分类或复核 |
| E2E 测试 | EML 附件到最终 JSON | 逐费用项输出和审计信息正确 |

### 9.2 数据集

准备最小但覆盖关键分支的样本：

1. 带文本正文和一个 PDF 附件的 EML。
2. 带嵌套 EML 或多个附件的 EML。
3. 数字 PDF 和扫描图片 OCR 响应。
4. 多费用行、合计一致和不一致的 Excel。
5. 正文与表格同时存在的 DOCX。
6. 已有账单 JSON：可更新、完全重复、字段冲突。
7. 同一单据重复提交。
8. OCR 混淆字符和低置信度结果。

对外部 OCR 和真实模型使用 fixture/mock，保证 CI 不依赖公网服务。

### 9.3 指标

- 字段级准确率：单号、日期、主体、费用、金额、币种。
- operation 分类 Precision/Recall。
- UPDATE 时未授权字段被修改的数量，目标为 0。
- 低置信度和冲突场景的复核召回率。
- 合法 JSON 输出率，目标为 100%。
- 每个场景的运行结果可重复一致。

## 10. 开发里程碑

### M0：项目骨架与契约

- 创建包结构、配置加载、Pydantic 模型和 CLI 入口。
- 定义 `Document IR`、`ExtractedDocument`、`BillRecord`、`OperationResult`。
- 验证：Schema 单元测试通过，CLI 能显示版本和帮助。

### M1：文件解析

- 完成文件路由、EML 递归解析、Excel、Word 和 PDF 辅助解析。
- 加入哈希、来源 ID、资源上限和错误记录。
- 验证：各格式 fixture 均生成稳定 IR。

### M2：OCR 与 Mock

- 实现 OCR 客户端、URL 文件发布接口和响应适配器。
- 提供录制样例和 Mock 服务，覆盖超时、空结果和异常格式。
- 验证：契约测试通过，失败可被流程分类处理。

### M3：抽取与标准化

- 实现 LangChain structured extraction、提示词和 Pydantic 校验。
- 实现费用名称、币种、日期、金额和付款方式标准化。
- 实现证据绑定和 deterministic fake extractor。
- 验证：黄金样本字段级结果符合预期，非法输出被拦截。

### M4：规则、匹配与合并

- 实现唯一候选匹配、重复检测、AR/AP 和字段级 patch。
- 实现 JSON 历史账单仓库。
- 验证：CREATE、UPDATE、NO_CHANGE、PENDING_REVIEW 四类分支测试通过。

### M5：LangGraph 与复核

- 将节点接入 LangGraph，处理重试、异常和复核状态。
- 输出 trace、warnings、confidence 和人工复核原因。
- 验证：端到端状态流转可复现，失败任务仍输出合法 JSON。

### M6：Harness、文档与交付

- 补齐端到端测试、评估脚本、样本数据和 README。
- 验证安装、运行、测试和环境变量说明完整。
- 生成代码 ZIP，排除缓存、临时文件和密钥。
- 验证：全新环境按 README 可完成安装、测试和示例运行。

## 11. 验收清单

- [x] EML 正文、邮件头和附件可解析，附件可按格式递归处理。
- [x] PDF 和图片通过 OCR API 客户端处理，失败可重试或复核。
- [x] Excel、Word 的正文和表格可进入统一 IR。
- [x] 单据编号、费用、金额和币种可从黄金样本中正确提取。
- [x] 每个费用项目生成一条输出记录。
- [x] 费用标准名称与原始名称同时保留。
- [x] 按四级优先级匹配已有账单，多候选不误更新。
- [x] UPDATE 只修改明确变化字段，其余字段继承。
- [x] AR/AP 结合记账主体、签发方、接收方和收费方向判断。
- [x] 重复、冲突、合计不平、OCR 异常和低置信度可识别。
- [x] 输出为合法 JSON，金额为数字，未知字段为 `null`。
- [x] 每个结果具备来源证据、处理依据和版本信息。
- [x] Harness 可离线执行，覆盖各格式、OCR 失败和关键业务分支。
- [x] README 包含配置、运行、测试、样本和已知限制。
- [x] 项目使用 Python，并实际集成 LangChain/LangGraph。

## 12. 风险与待确认事项

### 已制定默认策略的风险

1. OCR 服务只接受 URL：首版抽象 `FilePublisher`，测试使用 Mock，部署时接入对象存储或内网临时文件服务。
2. 现有账单 JSON 结构未知：先定义内部 `BillRecord` 和外部适配器，拿到真实样本文档后只调整适配器。
3. 信用单方向定义：从负数金额、Credit/Refund/Reversal 等明确语义及单据类型保守推断 `CREDIT`；无法确定时送复核。
4. 地址、联系人等抬头信息可能相似：匹配以业务键为主，不用地址或自由文本作为唯一依据。

### 建议向业务方确认

1. 已有账单 JSON 的完整字段和唯一标识。
2. `accounting_entity` 的来源、格式和同名主体消歧规则。
3. Credit Note 对 AR/AP 和金额符号的正式处理规则。
4. 金额内部是否包含税额，费用行金额是含税还是未税。
5. 匹配时日期是否允许容差，主体名称是否允许别名映射。
6. 自动入账的置信度阈值和必须人工复核的业务场景。
7. OCR 服务返回结构、错误码、并发限制和数据保留要求。

以上事项未确认前，系统应通过接口、配置和 `PENDING_REVIEW` 保持可运行，不阻塞主体开发。

## 13. 当前实施状态

截至 2026-10-21，已完成可在本地独立验证的首版实现：

- M0-M5 已完成：项目骨架、多格式解析、OCR 客户端、LangChain 抽取入口、LangGraph 编排、规则校验、收费方向、匹配、增量更新和复核分支。
- M6 已完成：真实样本、端到端测试、评估脚本、README、环境配置和代码包。
- 真实 Debit Note 可稳定提取 3 条费用，明细合计与 `total` 一致。
- 真实 Arrival Notice 可提取 `On Carriage Additional` 245 USD。
- 真实 EML 可解析正文 `H/C $50`，并递归读取 MBL/HBL 两个 PDF 附件。
- 当前 Harness 12/12 通过；pytest 为 57 passed、1 skipped，跳过项是需要显式启用的在线 LLM 回归。
- `ruff check .` 通过。
- 单段和多段 EML 正文均支持；源文件哈希随账单持久化，用于重复投递识别。
- OCR 原文会检查 `0/O`、`1/I` 疑似混淆；命中后强制进入 `PENDING_REVIEW` 并保留证据。
- 旧版 `.doc` 在 Windows 优先使用 Word COM 转换，LibreOffice 作为结构化后备；二者不可用或失败时使用 antiword 提取纯文本，并明确标记表格结构不可用。

尚未完成的外部依赖验证：

- 图片样本的在线 OCR 端到端识别需要将本地文件发布为 OCR 服务可访问的 URL。客户端、重试、响应适配和后续校验已经实现；当前尚未配置公开对象存储。
- 真实 LLM 在线调用需要 `LLM_API_KEY` 或 `OPENAI_API_KEY`，在线回归还需设置 `RUN_LIVE_LLM_TESTS=1`。LangChain structured-output 链已创建并通过无网络构造检查，默认离线运行使用确定性抽取器。
- `dist/shipping-bill-agent.zip` 是 2026-10-20 的旧快照，包内缺少后续新增源码和测试；如作为交付物使用，应在最终改动后重建并验证。


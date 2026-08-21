# Corpus 格式规范

本文档定义 LS-DYNA Manual to Markdown 生成的 Manual Corpus 目录结构与数据格式，是 `manifest` 模块与输出校验的实现依据。

Corpus schema v0.1 已完整实现。R17 四册验收生成 2,333 个非空 Markdown，
manifest 路径一一对应，8,186 个计划页面全部具有 PageIR。冻结验收基线位于
`r17-corpus-acceptance-v0.1.json`。

## 1. 目标与范围

用户提供合法获得的指定版本 LS-DYNA Keyword Manual 和/或 Theory Manual，并配置自己的解析 API 或本地模型运行时。项目在本地转换为结构稳定、来源可追溯、适合 LLM 读取的 Markdown Corpus。

本文档定义目标 Corpus 格式。当前 PageIR v0.2 已支持 `PageIR → SectionIR → KeywordIR/TheoryIR → 保守 Markdown` 重建。Keyword 的 Card 与 Variable Description 语义结构、Theory 的数字层级与 title-anchor 所有权、两类 Markdown renderer 和统一 manifest 均已接入。PageIR 保存表格 rowspan / colspan，Markdown 语义层使用不复制源文本的确定性矩形投影；无法确认的 Keyword 结构仍保留为 Source Material。`build` 一键执行 inspection、可续跑 parsing 和 reconstruction。

v0.1 是格式转换与结构重建工具，不是翻译器、总结器或技术内容改写器。最终 Markdown 保持 Manual 原始语言，Parser 与 LLM 不得添加解释、翻译、工程常识、推断结论或原文不存在的技术信息。

v0.1 不涉及 RAG、MCP、`.k` 文件解析、Keyword Validator、LSP、Embedding、知识图谱与多版本比较。

实际回归状态和已知 Markdown 边界见 `project-status.md`。Corpus 规范描述产物
形状，不代表每份输入 PDF 都能无 warning 生成完整语义条目。

## 2. 源文档结构背景

与 Corpus 设计直接相关的 Manual 结构事实：

- Keyword Manual 按 Volume 组织，Corpus 在文件系统层面保持 Volume 来源边界；Theory Manual 使用独立的 `theory` 文档身份；
- Manual 具有自身印刷页码（章-页格式，如 `2-131`），与 PDF 页面序号是两个概念；
- Keyword 条目可能跨页；
- Keyword 条目包含 Card 表格、Variable Description、Remarks 等结构。

## 3. 目录结构

最终 Corpus 只包含以下可分发产物。解析过程中产生的 raw artifact、PageIR、checkpoint 与临时 transport PDF 属于 workspace 本地中间产物，由 `parser-interface.md` 定义，不进入 Corpus。

```text
corpus_root/
├── corpus.yaml
├── manifest.jsonl
├── markdown/
│   ├── volume-1/
│   │   ├── AIRBAG/
│   │   ├── CONTROL/
│   │   └── ...
│   ├── volume-2/
│   │   ├── EOS/
│   │   ├── MAT/
│   │   └── ...
│   ├── volume-3/
│   │   └── ...
│   └── theory/
│       ├── 2.5.md
│       └── 22.3.1.md
└── reports/
    ├── acceptance.json       # 配置 quality gate 时生成
    ├── summary.json
    ├── issues.jsonl
    └── text_layer_comparison.json
```

Markdown 文档按 `volume → family → keyword` 三级目录组织。Volume 层应保留，以维持源文档的卷级来源边界；Family 层应保留，用于控制单目录规模并便于人工浏览。全部 Markdown 平铺的方案不采用。

Theory 文档使用独立的 `markdown/theory/<section_id>.md` 路径。Theory 的数字层级、
父子关系和 block 保留规则见 `theory-corpus-contract.md`，不套用 Keyword 的
family、Card 或变量目录结构。

Family 目录不生成 `index.md`。机器索引由 `manifest.jsonl` 承担，人工浏览由目录结构承担。

### 3.1 条目与文件的关系

Manual 中作为一个完整参考条目出现的内容对应一个 Markdown 文件。

以基础 Keyword 加 OPTION 方式统一描述的条目，其基础形式与全部 Option 保存在同一个 Markdown 文件中，不得因存在 Option 自动拆分文件。仅当 Manual 将两个名称作为两个独立参考条目分别描述时，才生成两个文件。

Theory 条目按 SectionMap 的 `kind == "theory"` 单位生成文件；相邻父子章节不合并，
也不因共享边界页自动拆分或删除原文。

## 4. 字段定义

以下字段在全部规范文档与最终输出中保持一致。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `keyword_id` | string | 基础 Keyword 名去掉开头 `*` 后的规范化名称，如 `MAT_EXAMPLE`，不使用 legacy 编号 |
| `name` | string | 完整 Keyword 名，含 `*`，如 `*MAT_EXAMPLE` |
| `family` | string | Keyword 族名，不带 `*`，如 `MAT` |
| `legacy_ids` | list[string] | 传统编号列表，如 `["MAT_001"]`，无传统编号时为 `[]` |
| `options` | list[string] | 条目内描述的 Option 列表，如 `["OPTION_A"]`，基础形式由 `name` 表示，不使用空字符串 |
| `volume` | int | 卷号，如 `2` |
| `source_pages` | list[object] | 来源页列表，每项含 `pdf_page` 与 `manual_page` |
| `markdown_path` | string \| null | 输出 Markdown 相对路径，未生成可靠 Markdown 时为 `null` |
| `status` | string | `success` / `warning` / `failed` 之一 |

Theory 记录使用 `section_id`、`section_number`、`title` 和 `parent_section_id` 替代
Keyword 专用字段，具体 JSON 形状见 `theory-corpus-contract.md`。两种记录共用
同一个 manifest，不建立第二套索引。

`source_pages` 每项的定义：

- `pdf_page`：PDF 页面序号，整数，从 1 开始，应存在；
- `manual_page`：Manual 印刷章-页编号，字符串，如 `"2-131"`，无法可靠识别时允许为 `null`。

`manual_page` 是印刷标签而非卷内唯一键：不同 release 可能在同一卷中重置或重复印刷页码。来源定位与排序以 `pdf_page` 为权威，`manual_page` 用于人工对照。

`source_pages` 由解析前的确定性阶段（PageMap / SectionMap，定义见 `parser-interface.md`）产生，是候选页范围：相邻条目可能共享边界页，范围内也可能包含 `manual_page` 为 `null` 的页面。`manifest.jsonl` 仍为来源追踪的权威索引。

## 5. corpus.yaml

记录语料库级元数据与构建信息，不保存配置文件路径、`job_url`、API Key 或任何认证信息。

```yaml
schema_version: "0.1"
manual:
  product: "LS-DYNA Manuals"
  release: "R17"
  documents:
    - document_id: "keyword-volume-1"
      manual_type: "keyword"
      volume: 1
      name: "Keyword Manual Volume I"
      source_file: "LS-DYNA_Manual_Vol_I_R17.pdf"
      pdf_page_count: 4301
      sha256: "…"
      support_level: "verified"
    - document_id: "theory"
      manual_type: "theory"
      volume: null
      name: "Theory Manual"
      source_file: "LS-DYNA_Manual_Theory_R17.pdf"
      pdf_page_count: 908
      sha256: "…"
      support_level: "verified"
builder:
  version: "0.1.0b1"
  # 记录实际构建使用的 Provider；取值取决于配置。
  parser_provider: "<provider used for the build>"
  parser_model: "<provider model name>"
  timestamp: "2026-08-20T00:00:00Z"
stats:
  entry_count: 1234
  family_count: 56
  status_success: 1220
  status_warning: 12
  status_failed: 2
  parse_total: 4500
  parse_completed: 4498
  parse_failed: 1
  parse_missing: 1
  issue_count: 98
  issues_by_severity:
    error: 1
    info: 64
    warning: 33
  issues_by_code:
    PAGE_PARSE_FAILED: 1
    KEYWORD_BOUNDARY_RESOLVED: 64
    VARIABLE_DESCRIPTION_UNMATCHED_TITLE: 33
  text_layer_sample_count: 12
  text_layer_issue_count: 0
  text_layer_divergence_count: 0
```

每个源 PDF 记录 `document_id`、`manual_type`、可空 `volume`、`source_file`、`pdf_page_count`、`sha256` 与 `support_level`，用于来源追溯。

`status_*` 统计 manifest 条目，`parse_*` 统计本次 ParsePlan 中的唯一页面，二者不是同一维度：

- `parse_total`：计划解析的唯一 `(document_id, pdf_page)` 数量；
- `parse_completed`：存在可供 Reconstruction 使用的 PageIR 的页面数；
- `parse_failed`：checkpoint 明确记录为 `failed` 的页面数；
- `parse_missing`：既无可用 PageIR、也没有明确失败状态的计划页面数。

`issue_count`、`issues_by_severity` 和 `issues_by_code` 是最终 `reports/issues.jsonl` 的聚合。`text_layer_*` 记录文本层抽样规模与差异数量。条目可能在某个来源页失败后仍生成可复核 Markdown，因此 `status_failed: 0` 不等价于 `parse_failed: 0`。

## 6. manifest.jsonl

Manifest 是 Corpus 级权威索引。每条记录只包含身份、来源、路径与状态信息，不包含 Card、变量等正文级内容。

每行一条 JSON：

```json
{
  "document_id": "keyword-volume-2",
  "manual_type": "keyword",
  "keyword_id": "MAT_EXAMPLE",
  "name": "*MAT_EXAMPLE",
  "family": "MAT",
  "legacy_ids": [],
  "options": ["OPTION_A"],
  "volume": 2,
  "source_pages": [
    {
      "pdf_page": 245,
      "manual_page": "2-131"
    }
  ],
  "markdown_path": "markdown/volume-2/MAT/MAT_EXAMPLE.md",
  "status": "success"
}
```

`status` 定义：

- `success`：条目生成完成，未发现结构问题，`markdown_path` 应存在；
- `warning`：条目生成完成，但存在需要复核的问题，只要生成可供人工复核的 Markdown，`markdown_path` 应存在；
- `failed`：无法生成可靠 Markdown，`markdown_path` 为 `null`。

Manifest 条目的 `status` 反映该条目的 SectionIR/KeywordIR/TheoryIR 重建结果。
Inspection-only issue（例如 `ANCHOR_CONFLICT`）仍会进入最终 `reports/issues.jsonl`
并使文档或整体构建进入 `warning`，但不会仅因导航证据冲突而把已经成功重建的条目降级；
应按 issue 中的 `document_id`、`section_id`、`keyword_id` 和页码定位复核。

`keyword_id` 不使用 legacy 编号。legacy 编号保存在 `legacy_ids` 字段中，不单独建立映射文件。

## 7. reports

每次构建生成一套报告，不按 Volume 分拆。

`summary.json` 顶层包含 `status`、release、文档列表以及与 `corpus.yaml.stats` 相同的条目、解析覆盖率、issue 和文本层统计。每个文档记录自己的 `status`、`parse_total`、`parse_completed`、`parse_failed` 和 `parse_missing`，用于定位不完整覆盖。公式表示差异记录为独立的 `TEXT_LAYER_FORMULA_REPRESENTATION_DIVERGENCE` warning。

最终 issue 集合汇总以下来源，不要求调用方再拼接中间报告：

- Inspection 的 `intermediate/<document_id>/issues.jsonl`；
- parse checkpoint 中的页面失败，转换为 `PAGE_PARSE_FAILED`；
- Reconstruction 与 PageIR 传播的问题；
- PDF 文本层验证问题。

存在无法生成的 manifest 条目或文档 ingest 失败时，整体 `status` 为 `failed`；存在 warning 条目、解析失败/缺页、best-effort 文档或任意 warning/error issue 时为 `warning`；其余情况才为 `success`。Inspection-only issue 不改变已成功重建条目的 manifest 状态，但仍参与整体状态判定。因此整体状态、manifest 条目状态和页面解析覆盖率必须一起判读。

`text_layer_comparison.json` 保存每个文档的抽样页、PageIR 与 PDF 文本层 token 计数、重叠数、双向 recall、非公式正文 token/recall、缺失 token 和相关 issue。它是验证报告，不是正文来源；任何 divergence 都不能静默覆盖 PageIR。

`issues.jsonl` 每行一条质量问题：

```json
{
  "document_id": "keyword-volume-2",
  "manual_type": "keyword",
  "volume": 2,
  "pdf_page": 245,
  "manual_page": "2-131",
  "keyword_id": "MAT_EXAMPLE",
  "section_id": "MAT_EXAMPLE",
  "severity": "warning",
  "code": "TABLE_STRUCTURE_UNCERTAIN",
  "message": "…"
}
```

字段定义：

- `document_id`、`manual_type`、`volume`、`pdf_page`、`manual_page`：问题发生的位置；
- `keyword_id`：问题归属的 Keyword。解析问题可能发生在 Keyword 边界恢复之前，无法归属时允许为 `null`；
- `section_id`：问题归属的 SectionMap/Theory 章节；Keyword 问题可为空，Theory 问题使用稳定章节 ID；
- `severity`：`info` / `warning` / `error` 之一；
- `code`：离散问题标记，取值应为 `parser-interface.md` 中登记的 issue code；
- `message`：问题说明。

页面级解析问题无法归属到具体 Keyword，示例如下：

```json
{
  "document_id": "keyword-volume-2",
  "manual_type": "keyword",
  "volume": 2,
  "pdf_page": 245,
  "manual_page": "2-131",
  "keyword_id": null,
  "section_id": null,
  "severity": "error",
  "code": "PAGE_PARSE_FAILED",
  "message": "…"
}
```

## 8. 下游索引策略

Corpus 是当前流水线的最终产物。下游索引器应以 `manifest.jsonl` 枚举条目，并按
以下合同消费：

- `success`：索引 `markdown_path` 指向的完整 Markdown，同时保存 identity、
  `source_pages` 和 manifest 状态；
- `warning`：仍可索引完整 Markdown，但必须保存 `review_required: true`，并按
  `document_id`、`keyword_id`/`section_id` 和来源页关联 `reports/issues.jsonl`；
- `failed`：不索引正文；当前合同要求其 `markdown_path` 为 `null`；
- 对 `warning` 条目，正文可用于检索和人工阅读，但 issue 所指的 Card slot、变量归属、
  边界或表格投影不得被下游提升为不带不确定性标记的权威结构化字段；
- `info` issue 保留审计证据，不要求把条目视为待审查；整体构建状态仍可能因其他
  文档级 warning/error 为 `warning`。

配置 `quality_gate.baseline` 时，重建还会写出 `reports/acceptance.json`。索引发布任务
应要求其中 `status == "passed"`；该报告验证基线统计、manifest、Markdown 内容摘要、
文件非空/路径一致性和配置的关键 evidence。未配置质量门的普通构建不生成该报告。

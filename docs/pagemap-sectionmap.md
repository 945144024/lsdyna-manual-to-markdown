# PageMap / SectionMap v0.1 契约

本文档冻结 Document Inspection 阶段的两个稳定中间产物。JSON Schema 位于
`schemas/`，回归基线位于 `regression-baseline-v0.1.json`。

该契约自 2026-08-17 起冻结。PageIR v0.2、SectionIR、KeywordIR、TheoryIR 和
Markdown 语义层均在本契约之上实现，不改变页面身份或 SectionMap 候选范围语义。
完整开发状态见 `project-status.md`。

## 支持范围

- 已验证并承诺支持 R12、R13、R14、R15、R16、R17 的 LS-DYNA Keyword Manual；
- 已验证并承诺支持官方存在的 R14、R15、R16、R17 Theory Manual；
- 其他 release 不拒绝运行，标记为 `best-effort` / `UNVERIFIED_RELEASE`，但不保证 PageMap / SectionMap 质量；
- 一次运行只允许一个 release，可输入任意非空文档子集：单册、任意 Keyword 卷组合、Keyword + Theory，或 Theory-only；

四册 R17 Inspection 已在 Linux/WSL 和原生 Windows Poppler 上分别执行，PageMap、
SectionMap 计数与质量结论一致。Windows stdout 固定按 UTF-8 解码，避免宿主代码页
改变 TOC、页脚或标题证据。

## 文档身份

页面的稳定身份是 `(document_id, pdf_page)`。`volume` 是 Keyword Manual 的卷号元数据，不参与 Theory Manual 身份。

| `document_id` | `manual_type` | `volume` |
|---|---|---:|
| `keyword-volume-1` | `keyword` | 1 |
| `keyword-volume-2` | `keyword` | 2 |
| `keyword-volume-3` | `keyword` | 3 |
| `theory` | `theory` | `null` |

同一运行中 `document_id` 必须唯一，所有文档的 `release` 必须相同。

## 产物布局

```text
intermediate/
├── keyword-volume-1/
│   ├── pagemap.json
│   ├── sectionmap.json
│   ├── toc_index.json
│   ├── legacy_alias_map.json
│   └── issues.jsonl
├── keyword-volume-2/             # 仅输入时存在
├── keyword-volume-3/             # 仅输入时存在
├── theory/                       # 仅输入时存在
└── inspection_summary.json
```

每个目录只对应一个源 PDF。产物保留在本地 workspace，可作为后续解析、回归和问题定位的输入。

## PageMap

PageMap 的 envelope 为：

```json
{
  "schema_version": "0.1",
  "document": {
    "document_id": "theory",
    "manual_type": "theory",
    "release": "R17",
    "volume": null
  },
  "pages": []
}
```

`pages` 的每个元素包含：

- `pdf_page`：从 1 开始的源 PDF 页序号；
- `manual_page`：印刷页码，如 `2-131`，无法可靠确定时为 `null`；
- `evidence`：`footer`、`anchor`、`interpolated` 或 `null`。

语义不变量：

- `pages` 按 `pdf_page` 严格递增且无重复；
- 一份文档的 `pdf_page` 覆盖 `1..pdf_pages`；
- `manual_page == null` 时 `evidence == null`；
- `manual_page` 允许重复、回退和跨章节重置，不作为页面主键；
- 下游不得用 Provider 识别的 footer 静默覆盖 PageMap。

## SectionMap

SectionMap 使用同一 `schema_version` / `document` envelope，`sections` 的每个元素包含：

- `section_id`：文档内唯一章节标识；
- `keyword_id`：Keyword 章节的规范化 ID，其他章节为 `null`；
- `name`：原始章节标题；
- `document_id`：所属源文档，必须与 envelope 一致；
- `volume`：Keyword 卷号，Theory 为 `null`；
- `kind`：`keyword`、`document` 或 `theory`；
- `parent_section_id`：层级父章节；
- `section_number`：Theory 的编号层级，如 `22.12.3`，其他类型通常为 `null`；
- `pdf_pages`：保守候选页集合；
- `manual_pages`：与 `pdf_pages` 逐项对应的印刷页码。

语义不变量：

- `section_id` 在文档内唯一；
- `pdf_pages` 非空、严格递增且均位于源 PDF 范围；
- `len(pdf_pages) == len(manual_pages)`；
- 相邻章节允许共享边界页，SectionMap 不是无重叠分区；
- `parent_section_id` 若非 `null`，必须引用同一文档中的章节；
- Keyword 章节满足 `keyword_id == section_id`；Theory / 文档章节的 `keyword_id == null`。

## 解析路径

Keyword 与 Theory 共用 PageMap、SectionMap 和质量门禁框架，但使用不同 inspection profile：

- Keyword profile：星号 Keyword 标题、family / appendix 章节、Keyword 页脚与别名；
- Theory profile：数字章节层级、title-case 页脚、同行或换行标题、跨行标题。

两条 profile 在统一的 `InspectionResult`、artifact writer、ParsePlan 和下游 `(document_id, pdf_page)` 身份处汇合。不要在下游重新按文档类型分叉。

## 质量门禁

每份文档必须同时满足：

- TOC 非空；
- SectionMap 非空；
- `sections_unresolved == 0`；
- 无 parser `error`；
- Keyword PageMap coverage 不低于 98%；
- Theory PageMap coverage 不低于 95%。

门禁失败时 `inspect` 失败，不得把空产物或低覆盖结果报告为成功。

## 冻结与演进策略

v0.1 从 2026-08-17 起冻结。允许新增非破坏性的外层报告字段，但不得在 v0.1 中删除字段、改变字段含义、改变页面身份或收紧到使既有 R12-R17 基线失效。破坏性变更必须发布新的 schema version，并保留显式迁移路径。

Python API 只接受显式 `ManualDocument` 和 `document_id`。`volume` 不得用于推断文档身份。

## 回归基线

版权安全基线只保存源 PDF hash、PageMap / SectionMap hash、数量与覆盖率等指标，不提交 Manual 内容或渲染页。当前基线覆盖 22 份文档：18 份 R12-R17 Keyword Manual 与 4 份 R14-R17 Theory Manual。

模型视觉复核对每份文档抽取 4 个确定性 SectionMap 起始页，共 88 页，核对章节标题、层级、印刷页码与 PDF 定位；结果全部通过。

候选回归：

```bash
manual-to-markdown-regression \
  --manuals-dir manuals \
  --output-dir workspace/regression \
  --render
```

严格比对冻结基线：

```bash
manual-to-markdown-regression \
  --manuals-dir manuals \
  --output-dir workspace/regression \
  --baseline docs/regression-baseline-v0.1.json \
  --require-reviewed
```

只有源 PDF、PageMap hash、SectionMap hash 和关键计数全部与已审阅记录一致时，运行结果才继承 `llm_review_status: passed`；任何差异都会回到 `pending`，要求重新审阅。

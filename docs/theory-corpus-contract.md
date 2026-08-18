# Theory Corpus 合同

本文定义 Theory Manual 的 Corpus v0.1 输出合同。它与 `docs/corpus-format.md`
中的 Corpus schema v0.1 共用 `corpus.yaml`、`manifest.jsonl` 和质量报告；本合同
只规定 `manual_type: theory` 条目的身份、内容边界和 Markdown 形状。TheoryIR 的
数字层级与 title-anchor 所有权重建、Markdown renderer、统一 manifest 接入和
端到端输出均已按本合同实现。

## 1. 核心决定

- 一个 `SectionMap.kind == "theory"` 的条目对应一个 Theory Markdown 文件。
- `section_id` 是条目稳定身份；`section_number` 是 Manual 的数字层级，保留为
  原文字符串，不转成浮点数。`parent_section_id` 保留 SectionMap 的父子关系。
- Theory 条目不拆成 Keyword、Card 或变量目录。Theory 的语义单位是按阅读顺序
  保存的 PageIR block 流，renderer 只做确定性排版。
- SectionMap 的 `pdf_pages` 是候选来源范围，不是精确内容分区。数字章节范围按章节
  深度终止，父子候选页重叠记录 `THEORY_HIERARCHICAL_PAGE_OVERLAP` info。TheoryIR
  只在唯一“章节号 + 标题”anchor 成立时切分 block 所有权，成功记录
  `THEORY_BOUNDARY_RESOLVED`；没有唯一 anchor 时记录
  `THEORY_TITLE_ANCHOR_MISSING` 并保守保留，不得静默丢弃或猜测归属。
- 原文优先。Parser 不翻译、不总结、不补写工程解释，不根据模型知识重建公式、表格
  或章节关系。

## 2. Manifest 记录

Theory 记录仍写入同一个 `manifest.jsonl`，并包含以下字段：

```json
{
  "document_id": "theory",
  "manual_type": "theory",
  "section_id": "22.3.1",
  "section_number": "22.3.1",
  "title": "Example Theory Section",
  "parent_section_id": "22.3",
  "source_pages": [
    {"pdf_page": 245, "manual_page": "22-17"}
  ],
  "markdown_path": "markdown/theory/22.3.1.md",
  "status": "success"
}
```

Theory 记录不写 `keyword_id`、`name`、`family`、`legacy_ids`、`options` 或
`volume`。`source_pages` 的页码定义与 Keyword 相同：`pdf_page` 是权威排序键，
`manual_page` 可以为 `null`，也不要求在整本 Manual 内唯一。

文件名使用经过稳定转义的 `section_id`；当 SectionMap 没有数字编号时使用
稳定的 `section_id` slug。不得用标题文本作为唯一文件身份，以免标题变更或字符
归一化造成路径漂移。

## 3. Markdown 结构

Theory 文件使用以下 Front Matter：

```yaml
---
document_id: theory
manual_type: theory
section_id: "22.3.1"
section_number: "22.3.1"
title: "Example Theory Section"
parent_section_id: "22.3"
manual_release: "R17"
source_pages:
  - pdf_page: 245
    manual_page: "22-17"
---
```

正文规则：

- 一级标题为条目标题；数字编号和标题按 PageIR/SectionMap 原文输出。
- 按 PageIR 的确定性 reading order 输出 `TextBlock`、`TableBlock`、`MathBlock`、
  `FigureBlock`、`HeaderBlock` 和 `FooterBlock` 的有效内容。重复页眉页脚可由
  SectionIR 标记为 ignored，但来源仍必须留在 provenance/block accounting 中。
- 表格使用 Markdown pipe table；`rowspan`/`colspan` 通过 PageIR 的确定性矩形投影
  输出，锚点文本只出现一次，覆盖位置为空，不复制语义内容。
- 公式优先输出已有 LaTeX 或原始数学表达；无法可靠恢复时输出原始文本并产生
  `MATH_PARSE_WARNING`，不得推导、补全或改写公式。
- 图片只输出带 PDF 页码的占位符，不描述图片内容。
- 无法归入确定性正文流的 block 进入 `## Source Material`，并保留来源引用。

Theory renderer 不生成摘要、关键词、变量解释、交叉链接或模型推断。父子章节
关系只通过 Front Matter 的 `parent_section_id` 表示；不会把子章节正文复制进父章节
文件，除非该 block 因共享边界证据不足而按本合同保守保留并标记 warning。

## 4. 状态与 issue

- `success`：候选页均可读取，正文 block 可按确定性顺序输出，没有需要人工复核的
  结构 issue。
- `warning`：Markdown 已生成，但存在未解决边界、空 PageIR、阅读顺序歧义、公式表示
  差异或 Source Material fallback；`markdown_path` 必须存在。
- `failed`：缺少必要 PageIR、页面身份校验失败，或无法保留任何可靠正文；
  `markdown_path` 为 `null`。

Theory 重点使用这些 issue code：

- `SECTION_PAGE_RANGE_MISMATCH`
- `SECTION_PAGEIR_MISSING`
- `SECTION_SHARED_BOUNDARY_PAGE`
- `SECTION_CONTENT_EMPTY`
- `THEORY_HIERARCHICAL_PAGE_OVERLAP`
- `THEORY_BOUNDARY_RESOLVED`
- `THEORY_TITLE_ANCHOR_MISSING`
- `THEORY_CONTENT_EMPTY`
- `READING_ORDER_AMBIGUOUS`
- `TABLE_SPAN_INVALID`、`PAGEIR_TABLE_SPAN_OUT_OF_BOUNDS`、`PAGEIR_TABLE_SPAN_OVERLAP`
- `MATH_PARSE_WARNING`
- `TEXT_LAYER_DIVERGENCE` 和
  `TEXT_LAYER_FORMULA_REPRESENTATION_DIVERGENCE`

任何 warning 或 failed 都必须进入同一次构建的 `reports/issues.jsonl`，不能只写在
Markdown 中。

## 5. 验收不变量

TheoryIR 与 renderer 当前满足以下不变量：

1. 每个 `kind=theory` SectionMap 条目最多生成一个 manifest 记录和一个 Markdown 文件。
2. 生成记录的 `source_pages` 与 SectionMap 候选范围一致，排序以 `pdf_page` 为准。
3. 在每个 TheoryIR 内，每个未忽略的 PageIR block 恰好进入一个正文位置或
   `Source Material`；ignored block 仍有 provenance，不得静默消失。
4. 共享边界、空页和阅读顺序歧义只提升状态并记录 issue，不改变原始 block 文本。
5. 相同 PageIR、SectionMap 和配置重复构建得到字节稳定的 Markdown、manifest 和报告。

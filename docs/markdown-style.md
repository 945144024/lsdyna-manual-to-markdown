# Markdown 格式规范

本文档定义 `lsdyna-manual-builder` 生成的 Keyword 与 Theory Markdown 文档的组织结构与格式规则，是 `markdown` 模块 renderer 的实现依据。

> 本文档描述当前 Corpus Markdown 格式。Keyword renderer 识别 Description、Purpose、Option、Card、变量说明区域、Remarks 与 References；等价 summary 和完全重复说明只在渲染层去重，来源仍参与 block accounting。Theory renderer 按数字层级与 title-anchor 所有权输出章节文件。PageIR 表格 span 在语义层投影为不复制源文本的矩形表格，无法确认的 Keyword 结构保留 Source Material fallback。Provider raw Markdown 不是最终产品格式。

## 1. 基本规则

- 原文保持：最终 Markdown 保持 Manual 原始语言。Parser 与 LLM 不得添加中文翻译、参数类型重复说明、默认值重复说明、工程解释、推断结论或模型世界知识。
- 条目即文件：Keyword 的完整参考条目和 Theory 的数字章节分别对应一个 Markdown 文件；同一 Keyword 内的基础形式与 Option 保存在同一文件中。
- 生成范围：SectionMap 中 `kind == "keyword"` 与 `kind == "theory"` 的条目均生成 Markdown，并共用同一个 manifest。
- 目录层级：Keyword 使用 `markdown/volume-N/family/keyword.md`，Theory 使用 `markdown/theory/<section_id>.md`，字段与路径规则见 `corpus-format.md`。
- 来源页码：Front Matter 的 `source_pages` 来自 SectionMap 候选范围，`manual_page` 可能重复或为 `null`，应原样输出，不做去重、补齐或“修正”；Provider raw Markdown 不直接作为本节定义的最终 Keyword Markdown。
- 图片：v0.1 不保存、不 OCR、不理解图片内容，仅输出占位符。

## 2. Front Matter

Manifest 是 Corpus 级权威索引，Front Matter 为单独读取 Markdown 文件提供最小自描述 metadata。两者的字段允许重叠，但应由同一个内部数据对象生成，不得独立维护。

Front Matter 固定包含：

```yaml
---
document_id: keyword-volume-2
manual_type: keyword
keyword_id: MAT_EXAMPLE
name: "*MAT_EXAMPLE"
family: MAT
legacy_ids: []
options:
  - OPTION_A
manual_release: "R13"
volume: 2
source_pages:
  - pdf_page: 245
    manual_page: "2-131"
---
```

Front Matter 不保存 parser model、builder version、build time、API 配置和统计信息。这些信息记录在 `corpus.yaml` 中。

## 3. 文档结构模板

以下示例为 synthetic 的虚构 Keyword，仅用于说明 Markdown 结构，不对应任何真实 LS-DYNA Keyword。示例中的 `[Manual source text]` 为模板占位文本，表示该位置填入 Manual 原文。

```markdown
---
document_id: keyword-volume-2
manual_type: keyword
keyword_id: MAT_EXAMPLE
name: "*MAT_EXAMPLE"
family: MAT
legacy_ids: []
options:
  - OPTION_A
manual_release: "R13"
volume: 2
source_pages:
  - pdf_page: 245
    manual_page: "2-131"
---

# *MAT_EXAMPLE

## Description

[Manual source text]

## Purpose

[Manual source text]

## Options

- 基础形式: *MAT_EXAMPLE
- `OPTION_A`: *MAT_EXAMPLE_OPTION_A

## Card Definitions

### Card 1

| Field | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Variable** | ID | P1 | P2 | FLAG | | | | |
| **Type** | I | F | F | I | | | | |
| **Default** | none | 0.0 | 0.0 | 0 | | | | |

### Card 1a

[Manual source text]

| Field | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Variable** | P3 | P4 | | | | | | |
| **Type** | F | F | | | | | | |

## Variable Descriptions

### ID

[Manual source text]

### P1

[Manual source text]

- `EQ.0`: [Manual source text]
- `GT.0`: [Manual source text]

### P2

[Manual source text]

### FLAG

[Manual source text]

## Remarks

1. [Manual source text]
2. $$ e = \frac{v_1 - v_2}{v_1} $$

## References

- [Manual source text]
```

`Description` 为可选小节，只接收 Keyword 标题之后、首个强语义锚点之前的连续原文块，不生成摘要或改写内容。

## 4. 格式规则

### 4.1 Card 表格

Card 表遵循 Manual 实际结构。常见数据行为 Variable、Type、Default 与 Remark；实际 Card 存在其他行时应按原文保留，不得为套用模板删除。

Card 表应保留 Manual 显示的全部字段槽位，包括空字段。标准 8-field Card 保留全部 8 个位置，空位置具有排版与字段位置意义，不得截断。

PageIR 的 `rowspan` / `colspan` 是源表格的逻辑结构。Markdown pipe table 无法表达合并单元格，因此 renderer 在进入 KeywordIR 行式规则和最终 Markdown 时使用确定性矩形投影：锚点单元格只保留一次，跨越位置为空，不复制变量或说明文本。源 PageIR 与 raw artifact 仍保留完整 span 证据。

当 OCR 将 `Variable`、`Type`、`Default` 合并到一行或单元格内时，只在 Card 槽位和字段类型形状同时成立时拆分。Card summary 仅在其变量槽位与 definition 完全一致且没有提供 definition 缺失字段时省略；否则两者都保留。

条件卡（如 Card 1a、Card 1b）作为独立三级小节与独立表格输出。条件说明只能来自 Manual，不得由 LLM 根据参数关系生成。

Card 条件说明会保留完整原文，并在 Card 小节下增加 `#### Conditions`。实现只识别原文中的 `=`、`EQ.`、`NE.`、`GE.`、`GT.`、`LE.`、`LT.` 等操作符，结构化字段用于定位与检索，不用于改写、解释或推导条件。

### 4.2 Variable Descriptions

每个变量使用三级标题。

Variable Description 只保存 Manual 中属于该变量的原始说明内容。类型与默认值已存在于 Card 表，不得重复输出；Manual 原变量说明本身包含这些信息时按原文保留。

表格单元格中的真实换行和 OCR 字面量 `\\n` 使用 `<br>` 渲染；常见 LaTeX 命令（如 `\\nabla`、`\\nu`）不得被当作换行。完全相同的说明片段可在渲染层省略重复副本，但每个来源块仍须参与 block accounting。

当 Manual 使用变量族泛称（例如 `Aij`、`Ai, Bi` 或 `Ci`）时，renderer 可根据同一条目 Card 变量目录做确定性前缀映射，并输出 `Applies to: ...`。无法安全映射时保留泛称原文并记录 issue。

### 4.3 取值条件

EQ. / NE. / LT. / GT. 等操作符只做结构化排版，操作符原样保留，不得重新解释、扩写或改写：

```markdown
- `EQ.0`: [Manual source text]
- `GT.0`: [Manual source text]
```

### 4.4 Options

同一 Manual 条目下的基础 Keyword 与 Option 保存在同一个 Markdown 文件中，以 `## Options` 小节列出，不得因存在 Option 生成独立文件。

### 4.5 公式

公式按两级 fallback 输出：

1. 优先输出 LaTeX，行内公式使用 `$...$`，独立公式使用 `$$...$$`；
2. 公式无法可靠恢复为 LaTeX 时，保存 Parser 得到的原始 Unicode 或文本表达，并在 `issues.jsonl` 记录 warning，不得补写、推导或重构公式。

### 4.6 图片占位

统一输出占位符，占位符不描述图片内容：

```markdown
> [Figure omitted. See source: PDF page 245, manual page 2-131.]
```

`manual_page` 为 `null` 时，只输出 PDF 页码：

```markdown
> [Figure omitted. See source: PDF page 245.]
```

### 4.7 References

References 小节仅收录 Manual 明确出现的交叉引用，不得根据模型知识补充相关 Keyword。

## 5. Theory Markdown

Theory Markdown 使用章节级 Front Matter：

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

正文以章节标题作为一级标题，随后按 PageIR 的确定性 reading order 保留正文、
表格、公式和图片占位。Theory 不生成摘要、Card、变量目录、解释性文本或模型推断；
无法可靠排序或归类的内容进入 `## Source Material` 并记录 warning。共享边界页、
父子章节和 block accounting 的完整规则见 `docs/theory-corpus-contract.md`。

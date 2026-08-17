# Markdown 格式规范

本文档定义 `lsdyna-manual-builder` 生成的 Keyword Markdown 文档的组织结构与格式规则，是 `markdown` 模块 renderer 的实现依据。

> 本文档描述的是目标 Corpus Markdown 格式。当前 v0.1 开发阶段尚未进入 Section Reconstruction 与最终 Markdown Renderer；Provider 返回的 raw Markdown（如 PaddleOCR-VL remote Markdown）不属于本文档定义的产品格式。实现状态见 `README.md` 与 `parser-interface.md`。

## 1. 基本规则

- 原文保持：最终 Markdown 保持 Manual 原始语言。Parser 与 LLM 不得添加中文翻译、参数类型重复说明、默认值重复说明、工程解释、推断结论或模型世界知识。
- 条目即文件：Manual 中作为一个完整参考条目出现的内容对应一个 Markdown 文件，同一条目内的基础 Keyword 与 Option 保存在同一文件中。
- 生成范围：仅 SectionMap 中 `kind == "keyword"` 的条目生成 Markdown；非 Keyword 文档章节不生成文件。
- 目录层级：`markdown/volume-N/family/keyword.md`，字段与路径规则见 `corpus-format.md`。
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

## 4. 格式规则

### 4.1 Card 表格

Card 表遵循 Manual 实际结构。常见数据行为 Variable、Type、Default 与 Remark；实际 Card 存在其他行时应按原文保留，不得为套用模板删除。

Card 表应保留 Manual 显示的全部字段槽位，包括空字段。标准 8-field Card 保留全部 8 个位置，空位置具有排版与字段位置意义，不得截断。

条件卡（如 Card 1a、Card 1b）作为独立三级小节与独立表格输出。条件说明只能来自 Manual，不得由 LLM 根据参数关系生成。

### 4.2 Variable Descriptions

每个变量使用三级标题。

Variable Description 只保存 Manual 中属于该变量的原始说明内容。类型与默认值已存在于 Card 表，不得重复输出；Manual 原变量说明本身包含这些信息时按原文保留。

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

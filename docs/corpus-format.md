# Corpus Format Specification v0.1

本文档定义 `lsdyna-manual-builder` 生成的 Manual Corpus 的目录结构与数据格式，是 `manifest` 模块与输出校验的实现依据。

## 1. 目标与范围

用户提供合法获得的指定版本 LS-DYNA Keyword Manual，并配置自己的解析 API。本项目将 Manual 在本地转换为结构稳定、来源可追溯、适合 LLM 读取的 Markdown Corpus。

本文档定义目标 Corpus 格式。当前 v0.1 开发阶段尚未完成 Section Reconstruction 与最终 Keyword Markdown Renderer，因此本节描述的是最终可分发 Corpus 的契约，不代表当前 `build` 命令已经产生这些产物。

v0.1 是格式转换与结构重建工具，不是翻译器、总结器或技术内容改写器。最终 Markdown 保持 Manual 原始语言，Parser 与 LLM 不得添加解释、翻译、工程常识、推断结论或原文不存在的技术信息。

v0.1 不涉及 RAG、MCP、`.k` 文件解析、Keyword Validator、LSP、Embedding、知识图谱与多版本比较。

## 2. 源文档结构背景

与 Corpus 设计直接相关的 Manual 结构事实：

- Manual 按 Volume 组织，Corpus 在文件系统层面保持 Volume 来源边界；
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
│   └── volume-3/
│       └── ...
└── reports/
    ├── summary.json
    └── issues.jsonl
```

Markdown 文档按 `volume → family → keyword` 三级目录组织。Volume 层应保留，以维持源文档的卷级来源边界；Family 层应保留，用于控制单目录规模并便于人工浏览。全部 Markdown 平铺的方案不采用。

Family 目录不生成 `index.md`。机器索引由 `manifest.jsonl` 承担，人工浏览由目录结构承担。

### 3.1 条目与文件的关系

Manual 中作为一个完整参考条目出现的内容对应一个 Markdown 文件。

以基础 Keyword 加 OPTION 方式统一描述的条目，其基础形式与全部 Option 保存在同一个 Markdown 文件中，不得因存在 Option 自动拆分文件。仅当 Manual 将两个名称作为两个独立参考条目分别描述时，才生成两个文件。

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

`source_pages` 每项的定义：

- `pdf_page`：PDF 页面序号，整数，从 1 开始，应存在；
- `manual_page`：Manual 印刷章-页编号，字符串，如 `"2-131"`，无法可靠识别时允许为 `null`。

`manual_page` 是印刷标签而非卷内唯一键：不同 release 可能在同一卷中重置或重复印刷页码。来源定位与排序以 `pdf_page` 为权威，`manual_page` 用于人工对照。

`source_pages` 由解析前的确定性阶段（PageMap / SectionMap，定义见 `parser-interface.md`）产生，是候选页范围：相邻条目可能共享边界页，范围内也可能包含 `manual_page` 为 `null` 的页面。`manifest.jsonl` 仍为来源追踪的权威索引。

## 5. corpus.yaml

记录语料库级元数据与构建信息，不保存配置文件路径、`base_url`、API Key 及任何认证信息。

```yaml
schema_version: "0.1"
manual:
  product: "LS-DYNA Keyword User's Manual"
  release: "R13"
  volumes:
    - name: "Volume I"
      source_file: "LS-DYNA_Manual_Volume_I_R13.pdf"
      pdf_page_count: 3846
      sha256: "…"
    - name: "Volume II"
      source_file: "LS-DYNA_Manual_Volume_II_R13.pdf"
      pdf_page_count: 1992
      sha256: "…"
builder:
  version: "0.1.0-dev"
  # 记录实际构建使用的 Provider；取值取决于配置。
  parser_provider: "<provider used for the build>"
  parser_model: "<provider model name>"
  timestamp: "2026-08-16T00:00:00Z"
stats:
  entry_count: 1234
  family_count: 56
  status_success: 1220
  status_warning: 12
  status_failed: 2
```

每个源 PDF 记录 `source_file`、`pdf_page_count` 与 `sha256`，用于来源追溯。

## 6. manifest.jsonl

Manifest 是 Corpus 级权威索引。每条记录只包含身份、来源、路径与状态信息，不包含 Card、变量等正文级内容。

每行一条 JSON：

```json
{
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

`keyword_id` 不使用 legacy 编号。legacy 编号保存在 `legacy_ids` 字段中，不单独建立映射文件。

## 7. reports

每次构建生成一套报告，不按 Volume 分拆。

`summary.json` 记录成功、警告与失败条目数量。只要存在 `failed` 条目，构建结果不得报告为全部成功。

`issues.jsonl` 每行一条质量问题：

```json
{
  "volume": 2,
  "pdf_page": 245,
  "manual_page": "2-131",
  "keyword_id": "MAT_EXAMPLE",
  "severity": "warning",
  "code": "TABLE_STRUCTURE_UNCERTAIN",
  "message": "…"
}
```

字段定义：

- `volume`、`pdf_page`、`manual_page`：问题发生的位置；
- `keyword_id`：问题归属的 Keyword。解析问题可能发生在 Keyword 边界恢复之前，无法归属时允许为 `null`；
- `severity`：`info` / `warning` / `error` 之一；
- `code`：离散问题标记，取值应为 `parser-interface.md` 中登记的 issue code；
- `message`：问题说明。

页面级解析问题无法归属到具体 Keyword，示例如下：

```json
{
  "volume": 2,
  "pdf_page": 245,
  "manual_page": "2-131",
  "keyword_id": null,
  "severity": "error",
  "code": "PAGE_PARSE_FAILED",
  "message": "…"
}
```

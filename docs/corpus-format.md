# Corpus Format Specification (v0.1 Draft)

本文档说明由 `lsdyna-manual-builder` 转换生成的标准本地 Manual Corpus 结构。

## 1. 顶层目录结构

转换完成后的本地 Corpus 目标结构如下：

```text
corpus_root/
├── corpus.yaml         # 语料库级全局元数据
├── manifest.jsonl      # 逐条 Keyword 的索引、来源与映射关系
├── markdown/           # 标准化 Markdown 文档（一个 Keyword 一个 .md 文件）
│   ├── SECTION_NAME/
│   │   └── KEYWORD_NAME.md
│   └── ...
└── reports/            # 转换过程报告与统计信息
    ├── summary.json
    └── issues.jsonl
```

## 2. 核心文件职责

### 2.1 `corpus.yaml`
记录整个构建产物的基本信息与配置快照：
- Manual 元数据（Release 版本、Volume 编号、发布年份等）；
- 构建环境与时间戳；
- 转换配置（使用的 Parser Provider、模型类型等）；
- 统计数据（总 Keyword 数量、总 Card 数量、跨页重构率等）。

### 2.2 `manifest.jsonl`
每行一条 JSON，作为高效检索和定位的索引文件，记录每一条 Keyword 的精细信息：
- `keyword_id`: 唯一标识符（如 `MAT_024` 或 `SECTION_KEYWORD`）；
- `keyword_name`: 完整 Keyword 名称（如 `*MAT_PIECEWISE_LINEAR_PLASTICITY`）；
- `section`: 所属大类或章节；
- `source_pdf`: 来源文件名称与页码范围（如 `[124, 127]`）；
- `markdown_path`: 对应的本地 Markdown 相对路径；
- `cards_count`: 包含的 Card 数量；
- `status`: 提取质量状态（`complete` / `has_warnings` 等）。

### 2.3 `markdown/`
存放根据 `markdown-style.md` 规则生成的标准化 Markdown 文本，作为上层应用（如检索、LLM 上下文注入等）的基础文档。

### 2.4 `reports/`
记录转换过程中的告警、未完全对齐的表格、疑似遗漏的卡片等，用于转换质量评估与人工复核。

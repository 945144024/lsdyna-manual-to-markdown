# Parser Interface & Provider Architecture (v0.1 Draft)

本文档说明解析器接口与后端 Provider 的抽象设计，以保证解析后端可插拔与可替换。

## 1. 设计目标

1. **避免厂商与模型绑定**：支持不同的文档解析方案（如自建 OCR/Vision Pipeline、云端文档解析 API、兼容 OpenAI Vision 的多模态大模型后端等）；
2. **统一中间结构**：无论底层采用哪种 Provider，均输出规范化的页面级/块级中间结果（`ParsedPage` / `DocumentBlock`），供后续重建阶段处理；
3. **用户自主配置**：解析 API 地址、认证环境变量均由用户在 YAML 配置中指定，系统不硬编码任何凭据。

## 2. 接口分层

```text
[ Manual PDF ]
       │
       ▼
[ Parser Provider (Interface) ] ── (可插拔: Local / Cloud / Custom)
       │
       ▼
[ Intermediate Representation ] ── (页面文本块、表格块、公式块、位置元数据)
       │
       ▼
[ Semantic Reconstruction ]     ── (LS-DYNA 专用关键字/卡片边界切分与跨页拼接)
       │
       ▼
[ Markdown & Corpus Output ]    ── (最终的 Markdown Corpus & Manifest)
```

## 3. Provider 适配器职责

每个 Provider 需要实现统一的基础接口规范（未来定义在 `lsdyna_manual.parser` 与 `lsdyna_manual.providers` 中）：
- `parse_document(path, options)`: 批量解析输入文档；
- `parse_page(page_bytes, page_number)`: 单页解析与提取；
- 错误重试与限流处理机制。

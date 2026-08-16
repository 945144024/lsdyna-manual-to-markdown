# Parser Interface & Provider Architecture v0.1

本文档定义解析模块的分层结构、Provider 接口与中间表示（IR），是 `parser` 与 `providers` 模块的实现依据。

## 1. 设计目标

1. Provider 可替换：Provider 只负责单页解析，文档调度逻辑不绑定具体 API；
2. IR 统一：任何 Provider 均输出同一份页面级与块级结构，供 reconstruction 消费；
3. 密钥隔离：配置文件只记录非敏感连接参数与密钥环境变量名，密钥不得进入日志、Corpus、报告或版本控制；
4. 失败可恢复：单页失败不中断整卷处理，构建结果不得掩盖失败条目。

## 2. 流水线分层

```text
[ Manual PDF ]
      │
      ▼
[ Document Parser / Orchestrator ]  打开 PDF、枚举页面、页面渲染、调度、retry、cache、checkpoint
      │
      ▼
[ Provider ]                        单页解析：multimodal parsing API → 结构化 IR
      │
      ▼
[ Parser Normalization ]            manual_page 恢复、issue 归并
      │
      ▼
[ Semantic Reconstruction ]         Keyword 边界、跨页合并、Card/变量/Remarks 结构恢复
      │
      ▼
[ Markdown & Manifest Output ]      markdown/ + manifest.jsonl + corpus.yaml
```

## 3. Provider

v0.1 的正式 Provider 为 `openai-compatible`，处理流程为：

```text
PDF page
→ render page
→ multimodal parsing API
→ structured IR
```

Provider 只负责单页解析，接口定义为：

```python
class ParserProvider(Protocol):
    name: str

    def parse_page(
        self,
        page_input: PageInput,
        options: ParseOptions,
    ) -> PageIR:
        ...
```

## 4. Document Parser / Orchestrator

Document Parser 负责文档级调度：

```text
打开 PDF
→ 枚举页面
→ 页面渲染
→ 调用 Provider.parse_page()
→ retry
→ cache
→ checkpoint
→ 聚合 PageIR
```

单页失败的处理流程：

```text
单页失败
→ 按配置重试
→ 仍失败
→ 记录 issue
→ 标记相关条目 warning / failed
→ 继续处理后续页面
```

构建完成后 `reports/summary.json` 应反映失败数量。只要存在 `failed` 条目，构建结果不得报告为全部成功。

## 5. 中间表示（IR）

### 5.1 PageIR

```python
class PageIR:
    pdf_page: int                 # PDF 页面序号，从 1 开始，应存在
    manual_page: str | None       # Manual 印刷章-页编号，如 "2-131"；无法识别时为 None
    blocks: list[Block]
    issues: list[ParseIssue]
```

`manual_page` 不要求 Provider 理解。Provider 忠实识别页面内容与页眉页脚，`manual_page` 由 Parser Normalization 层根据 Header 与 Footer 块提取。

### 5.2 Typed Block

Block 为 tagged 类型结构，至少包含：

```text
TextBlock
TableBlock
MathBlock
FigureBlock
HeaderBlock
FooterBlock
```

Table 应保存二维结构，不得降级为纯文本：

```python
class Cell:
    text: str
    row: int
    column: int

class TableBlock:
    rows: list[list[Cell]]
```

### 5.3 bbox

```python
bbox: tuple[float, float, float, float] | None
```

bbox 为可选字段。Provider 无法输出可靠版面坐标时，IR 不含 bbox 仍然有效；reconstruction 在 bbox 存在时可以使用。Corpus 不保存 bbox。

### 5.4 ParseIssue

IR 不定义数值 confidence。Page 与 Block 使用离散、可解释的问题标记：

```python
class ParseIssue:
    severity: str
    code: str
    message: str
```

`severity` 只允许 `info` / `warning` / `error`，与 `corpus-format.md` 中 `issues.jsonl` 的 `severity` 定义一致。

`code` 为离散问题标记，示例取值：

```text
PAGE_PARSE_FAILED
TABLE_STRUCTURE_UNCERTAIN
MATH_PARSE_WARNING
READING_ORDER_AMBIGUOUS
MANUAL_PAGE_NOT_FOUND
```

### 5.5 Header 与 Footer

Provider 阶段不删除页眉页脚，按 `HeaderBlock` 与 `FooterBlock` 输出。Reconstruction 根据跨页重复模式与 Manual 结构判断哪些内容应清理，哪些内容可用于 Keyword 归属与 Manual 页码恢复。

## 6. 配置与安全

配置分为两层：

- `provider`、`model`、`base_url` 等非敏感连接参数由配置文件提供；
- API Key 不直接写入配置文件。配置文件只记录 `api_key_env`，程序运行时通过该环境变量读取密钥。

密钥不得进入日志、Corpus、报告或版本控制。

```yaml
parser:
  provider: "openai-compatible"
  model: "your-model-name"
  base_url: "https://api.example.com/v1"
  api_key_env: "PARSER_API_KEY"
```

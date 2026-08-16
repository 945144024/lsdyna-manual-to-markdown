# Parser Interface & Provider Architecture v0.1

本文档定义解析模块的分层结构、各阶段职责与中间表示（IR），是 `parser` 与 `providers` 模块的实现依据。

## 1. 当前阶段目标

当前里程碑是 Reliable PageIR，而不是完整 Corpus 生成：

> 给定一组具有代表性的 LS-DYNA Manual 页面，稳定生成忠实、可验证、带来源定位的统一 PageIR；解析存在问题时形成明确的 ParseIssue，不得静默进入下游。

验证路径为：

```text
PDF page → 文档解析后端 → Provider Adapter → Canonical PageIR
```

Section Reconstruction 与 Corpus Generation 在 PageIR 通过真实页面验证之后实施。

本文档定义的 Canonical PageIR v0.1 是待真实 Manual 页面验证的核心接口，不是最终确定的 schema。在验证结论产出前，PageIR 字段不得扩张（见第 6 节）。

## 2. 设计目标

1. Provider 可替换：Provider 只负责单页解析的后端访问；Provider 原始输出与 Canonical PageIR 之间允许存在 provider-specific 表示与 Adapter；
2. IR 统一是软件接口契约：任何 Provider 的输出最终都转换为 Canonical PageIR，但不要求底层模型直接按 PageIR Schema 生成输出；
3. 密钥隔离：配置文件只记录非敏感连接参数与密钥环境变量名，密钥不得进入日志、Corpus、报告或版本控制；
4. 失败可恢复：单页失败不中断整卷处理，构建结果不得掩盖失败条目。

## 3. 流水线分层

```text
[ Manual PDF ]
      │
      ▼
[ Document Inspection / Segmentation ]   确定性：TOC、页眉页脚、PDF 文本层
      │
      ▼
[ SectionMap + PageMap ]                 Keyword→页范围；pdf_page↔manual_page
      │
      ▼
[ Page Parsing / Provider ]              文档调度、单页解析、Provider Adapter
      │
      ▼
[ Canonical PageIR ]
      │
      ▼
[ Normalization / Validation ]           manual_page 归一、文本层证据核对
      │
      ▼
[ Section Reconstruction ]               确定性结构重建
      │
      ▼
[ Markdown / Manifest Output ]
```

阶段边界原则：

- PDF 页码与 Manual 印刷页码的映射、Manual 条目（Keyword）的 PDF 页范围，以及 TOC、页眉页脚、PDF 文本能提供的确定性结构信息，应在解析前由 Inspection 阶段确定，不得由 VLM 或 Reconstruction 猜测 Keyword 边界；
- 模型负责文档感知，程序负责结构重建：Reconstruction 与 Markdown 渲染为确定性代码，不引入第二个 LLM 阶段重新阅读 PageIR 或组织 Markdown。

## 4. 阶段职责

### 4.1 Document Inspection / Segmentation

输入为 Manual PDF，全部处理使用确定性手段（PDF 文本层提取、TOC 解析、页眉页脚识别），不调用任何模型。

产出两份数据。

PageMap，逐页的页码映射：

```python
class PageMapEntry:
    pdf_page: int            # 从 1 开始
    manual_page: str | None  # 如 "2-131"；无法识别时为 None
    evidence: str | None     # "footer" | "anchor" | "interpolated" | None
```

`evidence` 记录该 manual_page 的确定性依据；当 `manual_page` 为 `None` 时，`evidence` 同为 `None`：

- `footer`：页码由该页页脚直接印出；
- `anchor`：该页为条目起始页，PDF 位置由正文 Keyword title 行定位，manual_page 来自对应 TOC 条目；
- `interpolated`：仅在满足局部插值条件时填充——相邻两个 anchor 同章，且 PDF 页码差与 manual 页码差相等；
- `None`：该页没有足够的页脚、锚点或局部插值证据，印刷页码未确定。

PageMap 不假设 `manual_page` 在卷内唯一或全局单调。部分 release 会在子章节起始处重置印刷页码（例如同一卷内出现多个 `12-1`）。`pdf_page` 是唯一且全局有序的来源定位键；`manual_page` 是印刷标签，用于人工对照，允许重复、回退或缺失。

SectionMap，Manual 条目到候选 PDF 页范围的映射：

```python
class Section:
    section_id: str                 # SectionMap 唯一标识；Keyword 条目与 keyword_id 相同
    keyword_id: str | None          # Keyword 条目为规范化名称（规则同 corpus-format.md）；文档章节为 None
    name: str                       # 完整条目名（Keyword 含 *）
    volume: int
    kind: str                       # "keyword" | "document"
    parent_section_id: str | None   # 文档子章节的顶层文档章节 section_id
    pdf_pages: list[int]            # 候选页集合，不构成严格分区
    manual_pages: list[str | None]
```

规则：

- SectionMap 表示候选页集合，不要求条目间构成无重叠分区；相邻条目通常共享一个边界页（前一条目可能在页面中部结束），证据不足时允许保留更大的保守重叠并记 issue，由 Reconstruction 收敛；
- SectionMap 可包含非 Keyword 文档章节（TOC 顶层非 Keyword 条目，如 INTRODUCTION、GETTING STARTED、REFERENCES、APPENDIX A..W）及其嵌套 TOC 子章节；文档章节 `keyword_id` 为 `None`，`section_id` 为层次化标识（如 `INTRODUCTION_MATERIAL_MODELS`）；仅 `kind == "keyword"` 的条目进入 manifest 与 Markdown 生成；
- Keyword 条目内部的 TOC 子标题（如 Card 名、Remarks）不是独立 SectionMap 条目，暂不选取；
- 条目起始页采用“印刷页脚候选 + 正文标题核验”的双证据定位：TOC 印刷页码反查页脚得到候选页后，该页必须出现正文标题证据（独立成行的 `*NAME`，或变体声明行，如 `_OPTION`、`_{OPTIONS}`、`_{OPTION1}_..._{OPTION6}`；缩进的 family 条目允许一个前置 family token，如 `_WELDTYPE_{OPTION}`）。候选页无标题时向前搜索标题证据，优先采用正文标题并记 `ANCHOR_CONFLICT`，以兼容 Manual TOC 自身存在的页码错误；running header 存在滞后与别名形态，仅作为归属与校验证据，不单独作为起始页定位依据；
- 无页脚区域按标题行单调搜索；第一遍只接受页面顶部附近的标题行，避免把 overview/family 页面正文列表中出现的子 Keyword 误判为条目起始页；
- 同一印刷页码可能在同一卷内出现多次。footer 反查保持一对多候选，按单调游标选择第一个候选，并在同页码候选中优先选择页面顶部有强标题证据的页；页码回退若正好发生在已定位的 section 边界，视为合法的子章页码重置而非冲突；
- TOC 文本与正文可能对同一字符使用 Unicode 兼容形式（如 `ﬂ` 与 `fl`）；标题比对前统一做 NFKD 归一化；
- 边界证据不足时保留偏大的页范围并记 issue（如 `SECTION_BOUNDARY_UNCERTAIN`），不得猜测精确边界；
- TOC 条目无法解析为页范围时记 issue 并跳过，不得虚构条目；
- PageMap 与 SectionMap 是 `manifest.jsonl` 来源追踪（`source_pages`）的数据来源；manifest 仍为权威索引，见 `corpus-format.md`。

### 4.2 Page Parsing：Provider 与 Adapter

Document Parser 负责文档级调度：打开 PDF、枚举页面、页面渲染、调用解析、retry、cache、checkpoint、聚合 PageIR。

解析后端分为两层：

```text
provider-specific raw result
（如 Paddle 管线的 Markdown/JSON、OpenAI-compatible 端点的模型输出）
      │
      ▼
[ Provider Adapter ]
      │
      ▼
Canonical PageIR
```

- Provider：负责单页的后端访问与传输，返回 provider-specific raw result；
- Adapter：将 raw result 转换为 Canonical PageIR，转换中发现的问题记为 ParseIssue；
- 对上层保留统一接口 `parse_page(page_input, options) -> PageIR`；raw result 与 Adapter 是该接口的内部实现；
- 后端能可靠使用 structured decoding 直接产出 PageIR 兼容 JSON 时，允许作为一种 Adapter 实现方式，但不得作为 Provider 接口的前提。

v0.1 的 Provider 类型为 `openai-compatible`；本地开发与测试后端为经 vLLM 部署的 PaddleOCR-VL（或其他 OpenAI 兼容端点）。

页眉页脚处理：Provider 阶段不删除页眉页脚，按 `HeaderBlock` 与 `FooterBlock` 输出，由下游阶段决定清理或利用。

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

### 4.3 Normalization / Validation

- `manual_page` 归一：`PageIR.manual_page` 以 PageMap 为基准填充与核对，不要求 Provider 理解印刷页码；
- PDF 文本层定位为 Evidence / Validation Source：将视觉解析结果与文本层证据比对，冲突时记录 issue（如 `TEXT_LAYER_DIVERGENCE`），不得静默覆盖视觉解析内容；
- v0.1 不定义任何自动修复规则；仅当某类错误模式被证明可以安全地确定性修复后，才允许增加 repair rule，且修复行为应记 issue 说明。

### 4.4 Section Reconstruction

- 输入：按 SectionMap 聚合的 Canonical PageIR 与 SectionMap 本身；
- 职责：Keyword 边界确认（与 SectionMap 不一致时收敛并记 issue）、跨页块合并、Card / Variable Description / Remarks 结构恢复；
- 全部为确定性代码。跨页表格首先尝试基于 bbox 列对齐等程序化方法；真实数据证明无法可靠确定性处理的结构单独讨论，不得预设第二个 LLM 阶段。

## 5. Header 与 Footer 的下游使用

Reconstruction 根据跨页重复模式与 Manual 结构判断哪些页眉页脚内容应清理，哪些内容可用于 Keyword 归属核对与 Manual 页码恢复。

## 6. Canonical PageIR v0.1（待验证 schema）

### 6.1 PageIR

```python
class PageIR:
    pdf_page: int                 # PDF 页面序号，从 1 开始，应存在
    manual_page: str | None       # Manual 印刷章-页编号，如 "2-131"；无法识别时为 None
    blocks: list[Block]
    issues: list[ParseIssue]
```

### 6.2 Typed Block

Block 为 tagged 类型结构，至少包含：

```text
TextBlock
TableBlock
MathBlock
FigureBlock
HeaderBlock
FooterBlock
```

### 6.3 Table 二维结构

Table 应保存二维结构，不得降级为纯文本：

```python
class Cell:
    text: str
    row: int
    column: int

class TableBlock:
    rows: list[list[Cell]]
```

### 6.4 bbox

```python
bbox: tuple[float, float, float, float] | None
```

bbox 为可选字段。Provider 无法输出可靠版面坐标时，IR 不含 bbox 仍然有效；reconstruction 在 bbox 存在时可以使用。Corpus 不保存 bbox。

### 6.5 ParseIssue

IR 不定义数值 confidence。Page 与 Block 使用离散、可解释的问题标记：

```python
class ParseIssue:
    severity: str
    code: str
    message: str
```

`severity` 只允许 `info` / `warning` / `error`，与 `corpus-format.md` 中 `issues.jsonl` 的 `severity` 定义一致。

### 6.6 字段约束

v0.1 不引入以下字段；是否需要由真实页面验证结论决定：

- rowspan / colspan；
- cell 级 bbox；
- reading order；
- source text 逐块保留；
- 数值 confidence 或其他 provenance 字段。

## 7. ParseIssue 生命周期

各阶段均可产生 issue，随 PageIR 与 SectionMap 向下游传递，最终汇入 `reports/issues.jsonl`（字段定义见 `corpus-format.md`），并影响条目 `status`。当前登记的 code：

- Pipeline / Build：`VOLUME_MISSING`、`VOLUME_INGEST_FAILED`、`PARSE_NOT_IMPLEMENTED`；
- Inspection：`SECTION_BOUNDARY_UNCERTAIN`、`TOC_ENTRY_UNRESOLVED`、`ANCHOR_CONFLICT`、`TOC_PAGE_TITLE_NOT_FOUND`；`MANUAL_PAGE_NOT_FOUND` 为预留 code；
- Parsing：`PAGE_PARSE_FAILED`；
- Adapter：`TABLE_STRUCTURE_UNCERTAIN`、`READING_ORDER_AMBIGUOUS`、`MATH_PARSE_WARNING`；
- Validation：`TEXT_LAYER_DIVERGENCE`。

`code` 为开放集合，新增 code 应在实现处登记语义。

Inspection 的中间产物 `intermediate/volume-N/issues.jsonl` 使用 `InspectionIssue` 序列化；对非 Keyword 文档章节，该中间文件的 `keyword_id` 字段填入 `section_id`（如 `INTRODUCTION_MATERIAL_MODELS`）。最终 Corpus 报告 `reports/issues.jsonl` 的 `keyword_id` 仍遵循 `corpus-format.md`：仅真正归属于 Keyword 的问题填入 Keyword ID，否则为 `null`。

## 8. Reliable PageIR 验证计划

第一轮真实测试不整卷运行，只选取约 10–20 个代表性真实页面，覆盖：

- 普通正文；
- 标准 Card；
- 条件 Card（Card 1a / 1b）；
- Variable Description；
- 跨页 Variable Description；
- 跨页表格；
- 公式；
- Figure；
- References；
- 一个 Keyword 结束并进入下一个 Keyword 的交接页。

观察重点：

- Card 的 8 个 field slot 是否完整保留，空字段是否丢失；
- Variable / Type / Default 行是否稳定成行；
- Card 条件说明与表格的关联是否可恢复；
- 跨页结构在 raw result 中的实际表现形式。

验证结论用于决定 PageIR 字段的增减与 Reconstruction 算法，不用于评测模型综合分数。

## 9. 配置与安全

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

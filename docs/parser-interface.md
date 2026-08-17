# Parser 与 Provider 架构

本文档定义解析模块的分层结构、各阶段职责与中间表示（IR），是 `parser` 与 `providers` 模块的实现依据。已冻结的 PageMap / SectionMap v0.1 以 `pagemap-sectionmap.md` 为准。

## 1. 当前阶段目标

当前主链路已经贯通到首版 Keyword Corpus：

> 给定一组具有代表性的 LS-DYNA Manual 页面，稳定生成带来源定位的 PageIR，再按 SectionMap 重建 SectionIR / KeywordIR 和保守 Markdown；解析或重建存在问题时形成明确的 ParseIssue，不得静默进入下游。

验证路径为：

```text
PDF page → 文档解析后端 → Provider Adapter → Canonical PageIR
```

Section Reconstruction 与 Corpus Generation 已实现首版，但仍受真实分层回归约束。Theory 最终 Markdown、复杂条件排版和若干 OCR/renderer 缺陷仍在开发中。

本文档定义的 Canonical PageIR v0.1 是当前稳定接口；任何字段扩张都必须有真实页面证据和 focused tests（见第 6 节）。

## 2. 设计目标

1. Provider 可替换：Provider 只负责 transport batch 的后端访问；语义解析单位仍由 Document Parser 保持为唯一页面。Provider 原始输出与 Canonical PageIR 之间允许存在 provider-specific 表示与 Adapter；
2. IR 统一是软件接口契约：任何 Provider 的输出最终都转换为 Canonical PageIR，但不要求底层模型直接按 PageIR Schema 生成输出；
3. 密钥隔离：API Key 只存在于被 Git 忽略的本地配置和进程内存中，配置对象使用 `SecretStr` 隐藏明文，密钥不得进入日志、Corpus、报告、raw artifact、checkpoint 或版本控制；
4. 失败可恢复：单页失败不中断整卷处理，构建结果不得掩盖失败条目。

## 3. 流水线分层

```text
[ Manual PDF ]
      │
      ▼
[ Document Inspection / Segmentation ]   确定性：TOC、页眉页脚、PDF 文本层
      │
      ▼
[ SectionMap + PageMap ]                 Manual section→候选页；pdf_page↔manual_page
      │
      ▼
[ ParsePlan ]                            page 去重；transport batch 组织
      │
      ▼
[ Document Parser / Provider ]           页面调度、后端访问、raw result 获取
      │
      ▼
[ Provider-specific raw artifact ]       provenance / debug，不进入下游接口
      │
      ▼
[ Provider Adapter ]
      │
      ▼
[ Canonical PageIR ]
      │
      ▼
[ Normalization / Validation ]           manual_page 归一、结构校验、ParseIssue
      │
      ▼
[ Reliable PageIR validation gate ]      结构校验；真实页面回归持续进行
      │
      ▼
[ Section / Keyword Reconstruction ]     SectionIR、块级 KeywordIR、来源守恒
      │
      ▼
[ Markdown / Manifest Output ]           保守 renderer 已实现；语义小节初版完成
```

阶段边界原则：

- PDF 页码与 Manual 印刷页码的映射、Manual 条目（Keyword）的候选 PDF 页范围，以及 TOC、页眉页脚、PDF 文本能提供的确定性结构信息，应在解析前由 Inspection 阶段确定；Reconstruction 只在 PageIR block 中用强标题证据确认共享页的块归属，不重新推断候选页范围；
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

PageMap 不假设 `manual_page` 在文档内唯一或全局单调。部分 release 会在子章节起始处重置印刷页码。`(document_id, pdf_page)` 是跨文档唯一来源定位键；`manual_page` 是印刷标签，用于人工对照，允许重复、回退或缺失。

SectionMap，Manual 条目到候选 PDF 页范围的映射：

```python
class Section:
    section_id: str                 # 文档内唯一；Keyword 条目与 keyword_id 相同
    keyword_id: str | None          # 非 Keyword 章节为 None
    name: str                       # 完整章节名（Keyword 含 *）
    document_id: str                # keyword-volume-1/2/3 | theory
    volume: int | None              # Theory 为 None
    kind: str                       # "keyword" | "document" | "theory"
    parent_section_id: str | None
    section_number: str | None      # Theory 数字层级，如 22.12.3
    pdf_pages: list[int]            # 候选页集合，不构成严格分区
    manual_pages: list[str | None]
```

规则：

- SectionMap 表示候选页集合，不要求条目间构成无重叠分区；相邻条目通常共享一个边界页（前一条目可能在页面中部结束），证据不足时允许保留更大的保守重叠并记 issue，由 Reconstruction 收敛；
- Keyword profile 可包含非 Keyword 文档章节及其嵌套 TOC 子章节；Theory profile 保存数字章节层级与 `section_number`。这些章节的 `keyword_id` 为 `None`；仅 `kind == "keyword"` 的条目进入 Keyword manifest 与 Markdown 生成；
- Keyword 条目内部的 TOC 子标题（如 Card 名、Remarks）不是独立 SectionMap 条目，暂不选取；
- 条目起始页采用“印刷页脚候选 + 正文标题核验”的双证据定位：TOC 印刷页码反查页脚得到候选页后，该页必须出现正文标题证据（独立成行的 `*NAME`，或变体声明行，如 `_OPTION`、`_{OPTIONS}`、`_{OPTION1}_..._{OPTION6}`；缩进的 family 条目允许一个前置 family token，如 `_WELDTYPE_{OPTION}`）。候选页无标题时向前搜索标题证据，优先采用正文标题并记 `ANCHOR_CONFLICT`，以兼容 Manual TOC 自身存在的页码错误；running header 存在滞后与别名形态，仅作为归属与校验证据，不单独作为起始页定位依据；
- 无页脚区域按标题行单调搜索；第一遍只接受页面顶部附近的标题行，避免把 overview/family 页面正文列表中出现的子 Keyword 误判为条目起始页；
- 同一印刷页码可能在同一卷内出现多次。footer 反查保持一对多候选，按单调游标选择第一个候选，并在同页码候选中优先选择页面顶部有强标题证据的页；页码回退若正好发生在已定位的 section 边界，视为合法的子章页码重置而非冲突；
- TOC 文本与正文可能对同一字符使用 Unicode 兼容形式（如 `ﬂ` 与 `fl`）；标题比对前统一做 NFKD 归一化；
- 边界证据不足时保留偏大的页范围并记 issue（如 `SECTION_BOUNDARY_UNCERTAIN`），不得猜测精确边界；
- TOC 条目无法解析为页范围时记 issue 并跳过，不得虚构条目；
- PageMap 与 SectionMap 是 `manifest.jsonl` 来源追踪（`source_pages`）的数据来源；manifest 仍为权威索引，见 `corpus-format.md`。

### 4.2 Page Parsing：ParsePlan、Provider 与 Adapter

Document Parser 负责页面级调度：读取 ParsePlan、生成 transport batch、调用 Provider、保存 raw artifact、调用 Adapter、生成并缓存 PageIR。

语义解析单位是唯一的 `(document_id, pdf_page)`，不是 Keyword 或章节。SectionMap 中相邻章节可以共享边界页，因此候选页必须先按文档合并去重，再进入解析。

#### 4.2.1 ParsePlan

```python
class PagePlanEntry:
    document_id: str
    pdf_page: int
    manual_page: str | None
    candidate_sections: tuple[str, ...]
    volume: int | None
```

`manual_page` 来自 PageMap。`candidate_sections` 只作为 Inspection provenance 供后续 Reconstruction 使用，不进入 Provider request，也不影响 Adapter 对页面的理解。

连续的同文档页面可以组成 `ParseBatch`，用于多页 Provider API。已定位的
SectionMap 章节起点是批次软边界，批次不会跨过该起点；`max_batch_pages` 是每批
页面数的硬上限。远程和本地 Provider 当前默认值均为 1，以保持页面映射与故障
隔离一致；远程 Provider 可显式调高。相邻章节共享的边界页仍只解析一次，并归入以
该页为起点的新批次：

```python
class ParseBatch:
    batch_id: int
    document_id: str
    pdf_pages: tuple[int, ...]
    volume: int | None
```

`pdf_pages` 的顺序同时定义 Provider 多页结果中 `layoutParsingResults` 的顺序。batch 只是 transport optimization，不成为页面身份或缓存身份。

每批 Provider 调用在 `job.json` 中记录上传、等待、结果下载和总耗时。耗时
元数据不得包含 API Key 或 signed result URL。

#### 4.2.2 Provider 与 Adapter

```text
provider-specific raw result
（如 PaddleOCR-VL remote 的 JSONL / Markdown）
      │
      ▼
[ Provider Adapter ]
      │
      ▼
Canonical PageIR
```

- Provider：负责后端访问与传输，返回 provider-specific raw result；
- Adapter：将 raw result 转换为 Canonical PageIR，转换中发现的问题记为 ParseIssue；
- Provider 和 Adapter 都不接收 Keyword 归属信息，也不根据 Keyword 先验修改页面解析；
- 多页 Provider 结果必须按 ParseBatch 的页面顺序拆回逐页 raw artifact 与 PageIR，`pdf_page` 身份不得依赖模型输出推断；
- 当前仓库支持 `paddleocr-vl-remote`（百度 AI Studio job API）和
  `paddleocr-vl-local`（本地 PaddleOCR Pipeline + llama-server）。

当前实现通过 `DocumentParser.parse_raw_for_document()` 和 `DocumentParser.build_pageir_for_document()` 暴露页面解析流程。所有调用方必须显式传入 `document_id`；单页 `parse_page()` 接口尚未暴露为稳定 API。

#### 4.2.3 Raw artifact 与 workspace 布局

Provider raw JSON / JSONL / Markdown 属于 workspace provenance 与调试材料，不是下游稳定接口。当前 Paddle raw bundle 按以下结构保存：

```text
<workspace>/parsing/
├── state.json                     # page-level checkpoint
├── raw/
│   ├── .transport/<document_id>/  # 提交给 Provider 的临时 batch PDF
│   └── <document_id>/
│       └── paddleocr-vl-remote/
│           └── <model>/
│           └── batches/
│               └── batch_0001_job_<job-id>/
│                   ├── input.pdf
│                   ├── raw_result.jsonl
│                   ├── job.json
│                   ├── page_map.json
│                   └── pages/
│                       ├── <document_id>_page_000197.json
│                       └── <document_id>_page_000197.md
└── pageir/
    └── <document_id>/
        └── page_000197.json
```

具体根目录由调用方根据 `output.corpus_dir` / workspace 约定传入；生产代码不依赖操作系统临时目录。signed result URL 不写入 `job.json`。

#### 4.2.4 Cache / resume

Cache 以 page 为核心，transport batch 不进入缓存身份。

Raw cache 身份：

```text
source PDF fingerprint
+ document_id
+ pdf_page
+ provider
+ model
+ provider semantic identity
```

Provider semantic identity 只包含会影响模型输出内容的配置。`max_batch_pages`、`timeout`、`poll_interval`、`max_retries` 属于 transport 参数，不使 raw cache 失效。

`max_retries` 除网络异常外，也用于 Paddle API 明确返回“提交队列已满”且尚未
创建 Job 的响应；其他 HTTP 或任务失败不会自动重新提交，避免产生重复任务。

PageIR cache 在 raw cache 之上增加：

```text
Adapter identity
+ PageIR schema version
```

因此修改 Adapter 后可以复用已保存的 raw artifact 重新生成 PageIR，而不会重新请求 Provider。

当前状态值：

- `raw_done`：Provider raw artifact 已成功保存；
- `done`：PageIR 已成功生成并保存；
- `paused_quota`：Provider 配额耗尽，页面保持待恢复状态；
- `failed`：该页面解析失败，可在后续运行中重试。

`state.json` schema v0.2 还保存 transport batch 状态。Provider 创建 Job 后立即
记录 `job_id`；进程中断后优先轮询该 Job，不重复提交。恢复前会重新计算源 PDF
hash，并读取 raw / PageIR JSON 核对文档、页码、Provider 与 model 身份。仅在
本地 artifact 校验通过时才跳过页面。

解析进度以 ParsePlan 中的唯一页面数为分母，不以 batch 数为分母。终端同时
显示当前阶段、文档、SectionMap 候选章节和批次页段。配额耗尽是全局暂停条件：
停止提交后续 batch、原子保存 checkpoint，并以独立退出码返回；恢复不依赖
Provider 提供配额重置时间。

默认失败不中断其他页面或 batch；失败页面记录错误后继续处理后续页面。

#### 4.2.5 Header / Footer 与 manual_page

Provider 阶段不删除页眉页脚。Paddle Adapter 从结构化 layout 结果生成 `HeaderBlock` 与 `FooterBlock`。

`PageIR.manual_page` 的权威来源是 PageMap，不由 Provider Footer 反向决定。Provider Footer 保留为 `FooterBlock`，未来作为 Validation evidence。若 PageMap 与 Provider Footer 冲突，应记录 issue，不得让 Provider 静默覆盖 PageMap。

#### 4.2.6 Adapter 与 Reconstruction 的边界

Adapter 的职责是忠实映射：

```text
provider raw result → Canonical PageIR
```

Adapter 不执行 LS-DYNA-specific 结构修复。真实 Provider 输出可能将 Variable / Type / Default 拆成多个 TableBlock，或把 Card 标题放入第一列形成额外列；这些现象只应保留为 PageIR / raw artifact 观察结果，不应在 Adapter 中通过 Card 语义猜测、合并或删除。

无法可靠映射时，保留 raw artifact 并产生明确 ParseIssue。

页眉页脚处理：Provider 阶段不删除页眉页脚，按 `HeaderBlock` 与 `FooterBlock` 输出，由下游阶段决定清理或利用。

单页失败的处理流程：

```text
单页失败
→ 按配置重试
→ 仍失败
→ 记录 ParseIssue / failed state
→ 继续处理后续页面
```

构建完成后 `reports/summary.json` 应反映失败数量。只要存在 `failed` 条目，构建结果不得报告为全部成功。

### 4.3 Normalization / Validation

当前已实现：`PageIR` 结构校验与 `(document_id, pdf_page)` 身份校验，`manual_page` 由 PageMap 填入。

当前已实现 PDF 文本层与视觉解析结果的确定性抽样比对。默认对每个有 PageIR 的文档抽取首、中、尾最多 3 个 PDF 页面，使用 `pdftotext -layout` 得到文本层 token，与 PageIR 中的 TextBlock 和 TableBlock 单元格做 multiset overlap。结果写入 `reports/text_layer_comparison.json`，低于阈值时将 reconstruction 状态提升为 `warning`，但不会覆盖 PageIR 或自动修复原文。

- `manual_page` 归一：`PageIR.manual_page` 以 PageMap 为基准填充与核对，不要求 Provider 理解印刷页码；
- PDF 文本层定位为 Evidence / Validation Source：将视觉解析结果与文本层证据比对，冲突时记录 issue（如 `TEXT_LAYER_DIVERGENCE`），不得静默覆盖视觉解析内容；
- 文本层不可用、页数不足或 `pdftotext` 执行失败时记录 `TEXT_LAYER_PAGE_UNAVAILABLE` 或 `TEXT_LAYER_COMPARISON_SKIPPED`，保留已生成的 PageIR；
- `validation.text_layer_enabled`、`text_layer_sample_pages`、`text_layer_min_tokens` 与 `text_layer_min_visual_recall` 控制抽样与阈值；
- v0.1 不定义任何自动修复规则；仅当某类错误模式被证明可以安全地确定性修复后，才允许增加 repair rule，且修复行为应记 issue 说明。

### 4.4 Section / Keyword Reconstruction

当前已实现首版确定性重建：

- 输入：按 SectionMap 聚合的 Canonical PageIR 与 SectionMap 本身；
- SectionIR 保留候选页范围，并报告缺页、空页和共享边界页；
- KeywordIR 使用 `(document_id, pdf_page, block_index)` 作为来源引用；
- 共享边界页仅在后续 Keyword 标题可由单个 TextBlock 强匹配时切分，否则保留双方内容并记录歧义；
- 页眉页脚从正文流中移出，但仍保留为 `ignored_blocks` 来源证据；
- 明确的 `Purpose:`、`Available options are:`、`Card Summary:`、`Data Card Definitions:`、`VARIABLE | DESCRIPTION`、`Remarks:` 和 `References:` 锚点用于确定语义区域；
- Card 通过 `Card N` 文本与表内 Card 行聚合；同一 TableBlock 可按 Card 行拆为多个语义行区间，但原始表块只参与一次 block 守恒记账；
- Card 表区分 `summary` 与 `definition`。仅 definition 区间按 Card 表头的固定槽位提取 Variable、Type 和 Default；OCR 短行按表头补 `null`，不从 summary 表猜测缺失字段；
- Card 字段保留 `(document_id, pdf_page, block_index, row, column)` 单元格来源，非空变量按首次出现顺序去重形成 Keyword 变量目录；
- Card 条件文本从归属于该 Card 的原文块中提取为 `CardConditionIR`，支持 `=`、`EQ.`、`NE.`、`GE.`、`GT.`、`LE.`、`LT.`；结构化字段保留 variable/operator/values/raw/source_text/source，不改写完整原句；
- Card 与 Variable Description 的跨页表格只在存在明确续接证据时设置 `continuation_of` 并合并渲染；孤立续表保留原始块并记录 issue，不根据页面邻接关系猜测；
- 显式 Option 仅从 Option 列表读取；Variable Description 表格按变量目录拆为行区间，空首列续行归入当前变量；单独文本变量标题及其后续块在强匹配时归入 `VariableDescriptionIR`；
- `VariableDescriptionIR.applies_to` 保存变量族泛称到同一 Keyword 具体 Card 槽位的确定性映射，例如 `Aij` → `A10`/`A11`/`A20`，无法确认时不扩展；
- 不能由强规则归类的正文块保存在 `unclassified_blocks`，并通过守恒校验防止静默丢失。

Card 表头无有效槽位、缺少 Variable 行或出现重复语义行时分别记录 `CARD_DEFINITION_SLOT_HEADER_INVALID`、`CARD_DEFINITION_VARIABLE_ROW_MISSING` 或 `CARD_DEFINITION_ROW_AMBIGUOUS`，同时保留原表。Variable Description 无法匹配 Card 变量目录，或出现没有当前变量的续行时记录 `VARIABLE_DESCRIPTION_UNMATCHED_TITLE` / `VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN`，保留原始块。Markdown renderer 已将已确认结构输出为 Purpose、Options、Card Definitions、Variable Descriptions、Remarks 和 References 小节；跨页续表仅按显式 continuation 合并，更深层的版面推断仍按真实页面证据逐步增加。全部规则使用确定性代码；无法可靠确定的结构保留原始块并记 issue，不预设第二个 LLM 阶段。

## 5. Header 与 Footer 的下游使用

Reconstruction 根据跨页重复模式与 Manual 结构判断哪些页眉页脚内容应清理，哪些内容可用于 Keyword 归属核对与 Manual 页码恢复。

## 6. Canonical PageIR v0.1

当前代码已实现 v0.1 数据模型、JSON 序列化与基础结构校验。这里“v0.1”是当前软件接口边界；它已经用于真实页面回归，但在下一次正式发布前仍允许通过兼容方式补充字段。

### 6.1 PageIR

```python
class PageIR:
    document_id: str              # 源文档身份
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

- Pipeline / Build：`UNVERIFIED_RELEASE`、`DOCUMENT_INGEST_FAILED`、`PARSE_NOT_IMPLEMENTED`；
- Inspection：`SECTION_BOUNDARY_UNCERTAIN`、`TOC_ENTRY_UNRESOLVED`、`ANCHOR_CONFLICT`、`TOC_PAGE_TITLE_NOT_FOUND`；`MANUAL_PAGE_NOT_FOUND` 为预留 code；
- Parsing：`PAGE_PARSE_FAILED`；
- Adapter：`TABLE_STRUCTURE_UNCERTAIN`、`READING_ORDER_AMBIGUOUS`、`MATH_PARSE_WARNING`；
- PageIR / Validation：`PAGEIR_DOCUMENT_IDENTITY_MISMATCH`、`PAGEIR_INVALID_PDF_PAGE`、`PAGEIR_PAGE_IDENTITY_MISMATCH`、`PAGEIR_INVALID_BBOX`、`PAGEIR_INVALID_TABLE_ROW`、`PAGEIR_INVALID_TABLE_COLUMN`、`PAGEIR_INVALID_ISSUE_SEVERITY`；
- Reconstruction：`SECTION_PAGE_RANGE_MISMATCH`、`SECTION_PAGEIR_MISSING`、`SECTION_SHARED_BOUNDARY_PAGE`、`SECTION_CONTENT_EMPTY`、`KEYWORD_BOUNDARY_RESOLVED`、`KEYWORD_BOUNDARY_AMBIGUOUS`、`KEYWORD_CONTENT_EMPTY`、`KEYWORD_BLOCK_ASSIGNED_MULTIPLE_TIMES`、`KEYWORD_BLOCK_ACCOUNTING_MISMATCH`、`CARD_DEFINITION_SLOT_HEADER_INVALID`、`CARD_DEFINITION_VARIABLE_ROW_MISSING`、`CARD_DEFINITION_ROW_AMBIGUOUS`、`VARIABLE_DESCRIPTION_UNMATCHED_TITLE`、`VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN`；
- Validation：`TEXT_LAYER_DIVERGENCE`、`TEXT_LAYER_PAGE_UNAVAILABLE`、`TEXT_LAYER_COMPARISON_SKIPPED`。

`code` 为开放集合，新增 code 应在实现处登记语义。

Inspection 的中间产物 `intermediate/<document_id>/issues.jsonl` 使用 `InspectionIssue` 序列化，并显式包含 `document_id`、`manual_type` 与可空 `volume`。最终 Corpus 报告 `reports/issues.jsonl` 的 `keyword_id` 仍遵循 `corpus-format.md`：仅真正归属于 Keyword 的问题填入 Keyword ID，否则为 `null`。

## 8. 分层语义回归

当前语义回归使用独立的分层随机抽样 manifest，不整卷运行。对 Volume I、II、III 和 Theory 分别按短章节（1～2 页）、中章节（3～6 页）、长章节（7～40 页）抽取默认 `3/4/3` 个 SectionMap 章节，并用少量显式 anchor 补充低频结构。manifest 固定 seed、源 PDF hash、SectionMap 章节身份和候选页范围，输出到 `workspace/regression/<release>/semantic-sample/sample_manifest.json`。

抽样与检测命令：

```bash
lsdyna-manual sample-regression \
  --manuals-dir manuals \
  --release R17 \
  --intermediate-dir workspace/regression/r17/intermediate \
  --pageir-dir workspace/run_r17/parsing/pageir \
  --output-dir workspace/regression/r17/semantic-sample \
  --seed 20260817
```

每个样本检测：

- PageIR 是否覆盖完整候选页范围；
- SectionIR / KeywordIR block accounting 是否守恒；
- Card、条件、Variable Description、变量族和显式续表的归属；
- Markdown 质量候选，包括 Card summary/definition 双重输出、混淆标识符（如 `EO/E0`）、字面量 `\\n`、重复变量描述和 Source Material fallback；
- PDF 文本层 visual recall、warning/error 和 unresolved issue 数量。

`not_parsed` 只表示当前样本还没有 PageIR，不等同于解析失败。当前已知边界样本可通过重复 `--anchor DOCUMENT_ID:SECTION_ID` 加入，不改变分层随机选择。

第一轮抽样的结构覆盖目标包括：

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

验证结论用于决定 PageIR 字段与 Reconstruction 算法的下一步修订，不用于评测模型综合分数。当前暂停点和实际页数见 `docs/project-status.md`。

## 9. 配置与安全

`parser.provider` 可选 `paddleocr-vl-remote` 或 `paddleocr-vl-local`。本地 provider
强制 `max_batch_pages: 1`，由独立 PaddleOCR Python 环境运行完整版面解析，VLM
识别通过本机 `llama-server` 完成。自动准备必须同时满足配置中的
`auto_prepare_runtime: true` 与 CLI 的 `--allow-runtime-install`；普通解析不会
静默修改环境。准备逻辑可以下载配置的模型、布局模型和明确指定的 llama-server
归档，但不会安装 NVIDIA 驱动、CUDA/WSL，也不会推断二进制下载来源。

```yaml
parser:
  provider: "paddleocr-vl-remote"
  model: "PaddleOCR-VL-1.6"
  job_url: "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
  api_key: "<local secret>"
  timeout_seconds: 1800
  poll_interval_seconds: 5
  max_retries: 2
  max_batch_pages: 1
  # Local mode example:
  # provider: "paddleocr-vl-local"
  # max_batch_pages: 1  # local mode enforces this value
  # local:
  #   runtime_dir: "./.runtime/paddleocr-local"
  #   paddleocr_python: "./.runtime/paddleocr-local/venv/bin/python"
  #   llama_server_path: "/mnt/c/Users/<user>/.cache/lsdyna-manual-builder/llama-server.exe"
  #   llama_server_url: "http://127.0.0.1:8111/v1"
  #   model_source: "bos"  # huggingface / modelscope / aistudio / null are also valid
  #   paddlex_cache_dir: "./.runtime/paddleocr-local/paddlex"
  #   model_path: "/mnt/c/Users/<user>/.cache/lsdyna-manual-builder/PaddleOCR-VL-1.6-GGUF.gguf"
  #   mmproj_path: "/mnt/c/Users/<user>/.cache/lsdyna-manual-builder/PaddleOCR-VL-1.6-GGUF-mmproj.gguf"
  #   auto_prepare_runtime: false
  #   auto_start_server: true
```

配置模型使用 Pydantic `SecretStr`，Provider 的 dataclass 也禁止在 `repr` 中显示密钥。缺少 Key 时，只有 Provider 实例化失败；不调用远程 OCR 的 `inspect` 和当前 ingest-only `build` 可以在 `api_key: null` 下运行。

安全不变量：

- 不记录 API Key、Authorization header 或完整配置对象；
- 不将 signed result URL 持久化到 `job.json`；
- `corpus.yaml`、报告、PageMap、SectionMap、raw artifact、PageIR 和回归基线均不得包含凭证；
- 示例配置只能使用 `null` 或占位值，真实凭证必须放入 `configs/local*.yaml`、`configs/*.local.yaml` 或 `configs/*.secret.yaml`；
- 提交前必须扫描 tracked worktree 和 Git 历史。

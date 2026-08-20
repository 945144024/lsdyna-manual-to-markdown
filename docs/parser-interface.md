# Parser 与 Provider 架构

本文档定义解析模块的分层结构、各阶段职责与中间表示（IR），是 `parser` 与 `providers` 模块的实现依据。已冻结的 PageMap / SectionMap v0.1 以 `pagemap-sectionmap.md` 为准。

## 1. 当前阶段目标

当前主链路已经贯通到 Keyword / Theory Corpus：

> 给定 LS-DYNA Manual 页面，稳定生成带来源定位的 PageIR，再按 SectionMap 重建 SectionIR、KeywordIR / TheoryIR 和保守 Markdown；解析或重建存在问题时形成明确的 ParseIssue，不得静默进入下游。

验证路径为：

```text
PDF page → 文档解析后端 → Provider Adapter → Canonical PageIR
```

Section Reconstruction 与 Corpus Generation 已实现首版，并完成 R17 分层样本、独立 holdout 和四册完整构建验证。复杂条件排版和若干 OCR/renderer 边界仍需结合完整报告中的真实页面持续收敛。

本文档定义的 Canonical PageIR v0.2 是当前稳定接口；它保留 v0.1 页面身份和普通表格读取能力，并增加真实 R17 页面所需的 rowspan / colspan 表达。任何后续字段扩张都必须有真实页面证据和 focused tests（见第 6 节）。

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
[ Section Reconstruction ]               SectionIR、KeywordIR / TheoryIR、来源守恒
      │
      ▼
[ Markdown / Manifest Output ]           Keyword/Theory renderer、统一 manifest
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

若目标页面的 raw artifact 已全部存在并通过身份校验，`parse` 使用缓存 raw 离线重建
PageIR，不启动远程或本地 Provider。源 PDF content stream 没有可见绘制/文本操作的页面
记录 `SOURCE_BLANK_PAGE` info；非空源页得到空 PageIR 时记录 `PAGE_PARSE_EMPTY`，丢弃
本次空结果并 fresh retry 一次，第二次仍为空则按页面解析失败处理。

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

具体根目录由调用方根据 `output.corpus_dir` / workspace 约定传入；生产代码不依赖操作系统临时目录。signed result URL 不写入 `job.json`。Provider 若使用了受限 transport recovery，必须在 `job.json.transport` 中记录 recovery 类型和 block 数量；该审计元数据不参与 PageIR 内容推断。

逐页 `.md` 只用于人工调试 raw artifact：本地/远程 Provider 能提供原始 Markdown 时应原样保存；否则从结构化 `parsing_res_list` 的可见 block 内容生成确定性的调试投影。该 sidecar 不是 Canonical PageIR 输入，也不是最终 Corpus Markdown，缺失或历史空文件不得改变 JSON raw artifact、PageIR 或 cache 身份。

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
`manual_page` 不属于模型语义身份；若重新 Inspection 后 PageMap 的印刷页码映射发生
修正，Reconstruction 加载缓存 PageIR 时以当前 PageMap 为权威在内存中归一化
`manual_page`，不重新调用 Provider，也不改写原始缓存。

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

构建完成后 `reports/summary.json` 必须分别反映条目状态与 ParsePlan 页面覆盖率。页面失败转换为带 `(document_id, pdf_page)` 来源的 `PAGE_PARSE_FAILED`，即使受影响条目仍生成了可复核 Markdown，整体构建也不得报告为全部成功。

### 4.3 Normalization / Validation

当前已实现：`PageIR` 结构校验与 `(document_id, pdf_page)` 身份校验，`manual_page` 由 PageMap 填入。

当前已实现 PDF 文本层与视觉解析结果的确定性抽样比对。默认对每个有 PageIR 的文档抽取首、中、尾最多 3 个 PDF 页面，使用 `pdftotext -layout` 得到文本层 token，与 PageIR 可见内容做 multiset overlap。报告同时保存 raw visual recall 和排除 MathBlock/行内公式后的 prose recall；raw recall 低而 prose recall 达标时记录 `TEXT_LAYER_FORMULA_REPRESENTATION_DIVERGENCE`，普通正文差异仍记录 `TEXT_LAYER_DIVERGENCE`。两者都会将 reconstruction 状态提升为 `warning`，且不会覆盖 PageIR 或自动修复原文。

- `manual_page` 归一：`PageIR.manual_page` 以 PageMap 为基准填充与核对，不要求 Provider 理解印刷页码；
- PDF 文本层定位为 Evidence / Validation Source：将视觉解析结果与文本层证据比对，冲突时记录 issue（如 `TEXT_LAYER_DIVERGENCE`），不得静默覆盖视觉解析内容；
- 文本层不可用、页数不足或 `pdftotext` 执行失败时记录 `TEXT_LAYER_PAGE_UNAVAILABLE` 或 `TEXT_LAYER_COMPARISON_SKIPPED`，保留已生成的 PageIR；
- `validation.text_layer_enabled`、`text_layer_sample_pages`、`text_layer_min_tokens` 与 `text_layer_min_visual_recall` 控制抽样与阈值；
- 不根据文本层或模型常识自动改写 PageIR 原文；只有被真实样本证明安全的确定性结构规则才允许进入重建流程，且必要时记录 issue 说明。

### 4.4 Section / Keyword Reconstruction

当前已实现首版确定性重建：

- 输入：按 SectionMap 聚合的 Canonical PageIR 与 SectionMap 本身；
- SectionIR 保留候选页范围，并报告缺页、空页和共享边界页；
- KeywordIR 使用 `(document_id, pdf_page, block_index)` 作为来源引用；
- 共享边界页仅在后续 Keyword 标题可由单个 TextBlock 强匹配时切分，否则保留双方内容并记录歧义；
- 页眉页脚从正文流中移出，但仍保留为 `ignored_blocks` 来源证据；
- 明确的 `Purpose:`、`Available options are:`、`Card Summary:`、`Data Card Definitions:`、`VARIABLE | DESCRIPTION`、`Remarks:` 和 `References:` 锚点用于确定语义区域；
- Card 通过 `Card N` 文本与表内 Card 行聚合；同一 TableBlock 可按 Card 行拆为多个语义行区间，但原始表块只参与一次 block 守恒记账；
- Card 表区分 `summary` 与 `definition`。definition 区间按 Card 表头的固定槽位提取 Variable、Type 和 Default，并在强结构证据下恢复合并行；OCR 短行按表头补 `null`。summary 只用于补充 definition 中确实缺失且具有明确槽位的变量，不覆盖冲突字段；
- Card 字段保留 `(document_id, pdf_page, block_index, row, column)` 单元格来源，非空变量按首次出现顺序去重形成 Keyword 变量目录；
- Card 条件文本从归属于该 Card 的原文块中提取为 `CardConditionIR`，支持 `=`、`EQ.`、`NE.`、`GE.`、`GT.`、`LE.`、`LT.`；结构化字段保留 variable/operator/values/raw/source_text/source，不改写完整原句；
- Card 与 Variable Description 的跨页表格只在存在明确续接证据时设置 `continuation_of` 并合并渲染；孤立续表保留原始块并记录 issue，不根据页面邻接关系猜测；
- 显式 Option 仅从 Option 列表读取；Variable Description 表格按变量目录拆为行区间，空首列续行归入当前变量；单独文本变量标题及其后续块在强匹配时归入 `VariableDescriptionIR`；
- `VariableDescriptionIR.applies_to` 保存变量族泛称到同一 Keyword 具体 Card 槽位的确定性映射，例如 `Aij` → `A10`/`A11`/`A20`，无法确认时不扩展；
- Keyword 标题之后、首个强语义锚点之前的连续正文归入 `description_blocks`；不能由强规则归类的正文块保存在 `unclassified_blocks`，并通过守恒校验防止静默丢失。

Card 表头无有效槽位、缺少 Variable 行或出现重复语义行时分别记录 `CARD_DEFINITION_SLOT_HEADER_INVALID`、`CARD_DEFINITION_VARIABLE_ROW_MISSING` 或 `CARD_DEFINITION_ROW_AMBIGUOUS`，同时保留原表。Variable Description 无法匹配 Card 变量目录时记录 `VARIABLE_DESCRIPTION_UNMATCHED_TITLE`；没有 Card 目录但存在明确 `VARIABLE | DESCRIPTION` 表头时可索引变量，同时记录 `VARIABLE_DESCRIPTION_CATALOG_UNAVAILABLE`。唯一 O/0 关联记录 `VARIABLE_IDENTIFIER_CONFUSABLE_MATCH`，源文本保持不变。Markdown renderer 输出 Description、Purpose、Options、Card Definitions、Variable Descriptions、Remarks 和 References；跨页续表仅按显式或强连续形状合并。全部规则使用确定性代码；无法可靠确定的结构保留原始块并记 issue，不预设第二个 LLM 阶段。

### 4.5 Theory Reconstruction

Theory 使用与 Keyword 相同的 SectionIR 和 block 来源身份，但不进入 KeywordIR。
TheoryIR 已实现以下稳定字段：

```python
class TheoryIR:
    document_id: str              # 固定为 theory
    section_id: str
    section_number: str | None
    title: str
    parent_section_id: str | None
    source_pages: list[SourcePage]
    owned_sources: list[BlockSourceRef]
    content_blocks: list[SourcedBlock]
    ignored_blocks: list[SourcedBlock]
    issues: list[ParseIssue]
    status: str
```

Theory SectionMap 候选范围按数字章节深度终止；父子候选范围允许重叠，并以
`THEORY_HIERARCHICAL_PAGE_OVERLAP` 记录为信息证据。TheoryIR 在线性 block 流中查找
唯一的“章节号 + 标题”anchor，并从该 anchor 拥有到下一个 Theory 标题 anchor 之前：
父章节因此只保留首个子标题前的 introduction，兄弟章节在下一标题处交接。成功切分
记录 `THEORY_BOUNDARY_RESOLVED`；找不到唯一 anchor 时保守保留候选内容并记录
`THEORY_TITLE_ANCHOR_MISSING`。页眉页脚进入 `ignored_blocks`，全部归属用
`owned_sources` 保持 block accounting。TheoryIR 已用于重建和回归检测，并已接入
Theory Markdown renderer 与统一 manifest；完整输出规则见
`docs/theory-corpus-contract.md`。

## 5. Header 与 Footer 的下游使用

Reconstruction 根据跨页重复模式与 Manual 结构判断哪些页眉页脚内容应清理，哪些内容可用于 Keyword 归属核对与 Manual 页码恢复。

## 6. Canonical PageIR v0.2

当前代码已实现 v0.2 数据模型、JSON 序列化与表格 span 结构校验。`PageIR.from_dict` 仍可读取 v0.1 artifact；重新保存时统一写出 v0.2。PageMap / SectionMap 的 v0.1 页面身份和候选范围契约不变。

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
    rowspan: int = 1
    colspan: int = 1

class TableBlock:
    rows: list[list[Cell]]
```

`rows` 保存逻辑单元，而不是把 rowspan / colspan 覆盖的格子复制为新的源单元。`row` / `column` 是逻辑单元左上角坐标；`rowspan` / `colspan` 必须为正整数。下游需要矩形访问时使用确定性的 `table_grid_rows()` 投影：锚点保留原文，覆盖位置使用空 synthetic cell，不复制源文本。这样既保留原始表格结构，也不让现有行式语义规则猜测跨行/跨列内容。

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
    pdf_page: int | None = None
    manual_page: str | None = None
```

`severity` 只允许 `info` / `warning` / `error`，与 `corpus-format.md` 中 `issues.jsonl` 的 `severity` 定义一致。页级 issue 应在首次拥有可靠页面身份时填入 `pdf_page` 和 `manual_page`；旧 artifact 或非页级问题允许省略。Reconstruction 传播 issue 时优先使用 issue 自身的页级来源，仅在缺失时回退到所属条目的首个来源页。Inspection-only issue 会进入最终报告并影响文档/整体状态，但不自动改变已成功重建条目的 manifest `status`。

### 6.6 字段约束

v0.2 仍不引入以下字段：

- cell 级 bbox；
- reading order；
- source text 逐块保留；
- 数值 confidence 或其他 provenance 字段。

span 校验发现非法值、越界或覆盖重叠时分别记录 `PAGEIR_INVALID_TABLE_SPAN`、`PAGEIR_TABLE_SPAN_OUT_OF_BOUNDS` 或 `PAGEIR_TABLE_SPAN_OVERLAP`。Adapter 无法解析 HTML span 属性时记录 `TABLE_SPAN_INVALID`；合法 span 不再产生 `TABLE_STRUCTURE_UNCERTAIN`。

## 7. ParseIssue 生命周期

各阶段均可产生 issue，随 PageIR 与 SectionMap 向下游传递，最终汇入 `reports/issues.jsonl`（字段定义见 `corpus-format.md`）。除 Inspection-only 导航 issue 外，带有明确条目归属的 warning/error 会影响条目 `status`；Inspection-only issue 只影响文档/整体状态。当前登记的 code：

- Pipeline / Build：`UNVERIFIED_RELEASE`、`DOCUMENT_INGEST_FAILED`；
- Inspection：`SECTION_BOUNDARY_UNCERTAIN`、`TOC_ENTRY_UNRESOLVED`、`ANCHOR_CONFLICT`、`TOC_PAGE_TITLE_NOT_FOUND`；`MANUAL_PAGE_NOT_FOUND` 为预留 code；
- Parsing：`PAGE_PARSE_FAILED`、`PAGE_PARSE_EMPTY`、`SOURCE_BLANK_PAGE`；
- Adapter：`TABLE_STRUCTURE_UNCERTAIN`（仅保留给无法形成确定投影的结构）、`TABLE_SPAN_INVALID`、`READING_ORDER_AMBIGUOUS`、`MATH_PARSE_WARNING`；
- PageIR / Validation：`PAGEIR_DOCUMENT_IDENTITY_MISMATCH`、`PAGEIR_INVALID_PDF_PAGE`、`PAGEIR_PAGE_IDENTITY_MISMATCH`、`PAGEIR_INVALID_BBOX`、`PAGEIR_INVALID_TABLE_ROW`、`PAGEIR_INVALID_TABLE_COLUMN`、`PAGEIR_INVALID_TABLE_SPAN`、`PAGEIR_TABLE_SPAN_OUT_OF_BOUNDS`、`PAGEIR_TABLE_SPAN_OVERLAP`、`PAGEIR_INVALID_ISSUE_SEVERITY`；
- Reconstruction：`SECTION_PAGE_RANGE_MISMATCH`、`SECTION_PAGEIR_MISSING`、`SECTION_SHARED_BOUNDARY_PAGE`、`SECTION_CONTENT_EMPTY`、`KEYWORD_BOUNDARY_RESOLVED`、`KEYWORD_BOUNDARY_AMBIGUOUS`、`KEYWORD_CONTENT_EMPTY`、`KEYWORD_BLOCK_ASSIGNED_MULTIPLE_TIMES`、`KEYWORD_BLOCK_ACCOUNTING_MISMATCH`、`CARD_DEFINITION_SLOT_HEADER_INVALID`、`CARD_DEFINITION_VARIABLE_ROW_MISSING`、`CARD_DEFINITION_ROW_AMBIGUOUS`、`CARD_DEFINITION_CONTINUATION_ORPHAN`、`VARIABLE_DESCRIPTION_CATALOG_UNAVAILABLE`、`VARIABLE_DESCRIPTION_UNMATCHED_TITLE`、`VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN`、`VARIABLE_IDENTIFIER_CONFUSABLE_MATCH`、`THEORY_HIERARCHICAL_PAGE_OVERLAP`、`THEORY_BOUNDARY_RESOLVED`、`THEORY_TITLE_ANCHOR_MISSING`、`THEORY_CONTENT_EMPTY`；
- Validation：`TEXT_LAYER_DIVERGENCE`、`TEXT_LAYER_FORMULA_REPRESENTATION_DIVERGENCE`、`TEXT_LAYER_PAGE_UNAVAILABLE`、`TEXT_LAYER_COMPARISON_SKIPPED`。

`code` 为开放集合，新增 code 应在实现处登记语义。

Inspection 的中间产物 `intermediate/<document_id>/issues.jsonl` 使用 `InspectionIssue` 序列化，并显式包含 `document_id`、`manual_type` 与可空 `volume`。最终 Corpus 报告统一汇入 Inspection issue、checkpoint 页面失败、Reconstruction/PageIR issue 和文本层验证 issue。`reports/issues.jsonl` 的 `keyword_id` 仍遵循 `corpus-format.md`：仅真正归属于 Keyword 的问题填入 Keyword ID，否则为 `null`。

## 8. 分层语义回归

语义规则开发仍使用独立的分层随机抽样 manifest：对 Volume I、II、III 和 Theory 分别按短章节（1～2 页）、中章节（3～6 页）、长章节（7～40 页）抽取默认 `3/4/3` 个 SectionMap 章节，并用少量显式 anchor 补充低频结构。manifest 固定 seed、源 PDF hash、SectionMap 章节身份和候选页范围，输出到 `workspace/regression/<release>/semantic-sample/sample_manifest.json`。除此之外，R17 已执行四册完整 ParsePlan 和 Corpus 构建验收；抽样回归用于快速、可复现的规则验证，不能替代完整构建报告。

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

验证结论用于决定 PageIR 字段与 Reconstruction 算法的下一步修订，不用于评测模型综合分数。当前样本状态、实际页数和后续验收顺序见 `docs/project-status.md`。

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

本地 llama.cpp backend 的主路径仍是 PaddleX `chat.completions`。隔离 worker 只在同一可信 server origin 的 `/v1/chat/completions` 返回结构完全匹配的 HTTP 500（`code=500`、`type=server_error`、精确的 PEG-native message）时，才对同一识别 block 启动一次 SSE token-byte recovery：

1. 只接受当前 PaddleX 已确认的单个 user message：一个 `data:image/...;base64,...` 图片 part 后接一个非空 text part；远程 URL、文件路径、未知 part 或额外 message 均拒绝恢复；
2. 从 `/props` 要求 vision capability 和非空 `media_marker`，且 marker 不得出现在原始文本中；使用原始 messages 调用 `/apply-template`，要求得到唯一非空 prompt，prompt marker 数量必须和图片数量完全一致；
3. 用该 prompt、同一 base64 图片和等价生成参数调用流式非 OpenAI `/completion`；固定 `stream=true`、`n_cmpl=1`、`n_probs=1`、`return_tokens=true`，并逐项验证 `max_tokens -> n_predict`、`temperature=0`、`top_p`、repetition penalty、special-token、stop 和 seed；tools、response format、logprobs 或未知参数不得被静默丢弃；
4. 不读取 SSE 的 `content` 作为来源。每个 token event 必须具有连续计数、一个 token ID、一个 completion probability、相同 ID 的 top-1 candidate，以及合法的 `completion_probabilities[0].bytes`；程序按顺序累计这些 server `text_to_send` bytes。`id_slot` 是不透明的 llama.cpp 调度元数据，允许整体缺省、`null` 或有符号整数；一旦出现，其存在性和值必须在整个 token stream 中保持一致；
5. 只有已传输事件序列完整结束，且最后一个 SSE event 是结构完全匹配的 Content-only error（`code=500`）时，累计 bytes 才可作为该 block 的恢复输出。该条件只证明收到的 token stream 完整，不证明模型原计划的全部文本均已生成；native parser 可以在拒绝字符处提前终止。该 native stream 的 HTTP 状态仍为 200；正常 `stop`、`[DONE]`、非 SSE 响应、断流、非法 JSON、缺失/重复字段、token/probability 不一致、超出 token limit 或其他 final error 都必须保留原 PEG 页面失败；
6. byte 序列使用 UTF-8 `errors=replace` 解码，不猜测或补写无效 byte。成功恢复次数、transport 类型、精确解码输出和 replacement-character 计数写入本地 raw job/page metadata。Adapter 对每次恢复记录 `MODEL_OUTPUT_BYTE_RECOVERY` warning；若结构化 block 或 raw 恢复输出含 Unicode replacement character，再记录 `MODEL_OUTPUT_REPLACEMENT_CHARACTER`。

该路径不声称 `/completion` 绕过 llama.cpp 的最终内容解析器；相反，它只使用最终解析前已经由 server 发出的逐 token byte 证据，并以随后出现的精确 Content-only 失败作为传输终止证据。它不绕过 Paddle layout detection，也不使用 PDF 文本层补写模型结果。Paddle 后处理可能把包含拒绝字符的恢复输出投影为更短的结构化 block，因此 raw 恢复输出与 PageIR 投影必须同时可追溯，且该页面不能标记为无异常成功。后续规则不得凭上下文猜测缺失字符或补全被截断文本。

配置模型使用 Pydantic `SecretStr`，Provider 的 dataclass 也禁止在 `repr` 中显示密钥。缺少 Key 时，只有 Provider 实例化失败；`inspect` 可以在 `api_key: null` 下运行。一键 `build` 若所有请求 raw 已缓存也可离线继续，否则远程模式必须提供 Key。

安全不变量：

- 不记录 API Key、Authorization header 或完整配置对象；
- 不将 signed result URL 持久化到 `job.json`；
- `corpus.yaml`、报告、PageMap、SectionMap、raw artifact、PageIR 和回归基线均不得包含凭证；
- 示例配置只能使用 `null` 或占位值，真实凭证必须放入 `configs/local*.yaml`、`configs/*.local.yaml` 或 `configs/*.secret.yaml`；
- 提交前必须扫描 tracked worktree 和 Git 历史。

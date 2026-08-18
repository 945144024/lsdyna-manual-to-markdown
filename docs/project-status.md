# 项目开发状态

本文记录 `0.1.0-dev` 的开发状态、验证证据和未完成事项。面向用户的运行说明见 `README.md`；本文更偏向开发交接和回归记录。

## 里程碑

### 已完成

- Keyword Volume I-III 与 Theory Manual 的确定性发现、ingest 和文档身份解析。
- PageMap / SectionMap v0.1、JSON Schema、TOC 索引、legacy alias 和 inspection 质量门禁。
- R12-R17 回归基线：22 份 PDF、88 个模型辅助视觉复核页面。
- 页面级 ParsePlan：SectionMap 章节起点作为软边界，`max_batch_pages` 作为 transport 硬上限。
- 远程 PaddleOCR-VL Provider：进度输出、raw artifact、业务码/配额处理、checkpoint 校验和恢复；远程默认 batch size 为 1。
- 本地 PaddleOCR-VL Provider：PaddleOCR worker、官方 GGUF/mmproj、PaddleX layout model 和 llama-server 生命周期；本地单页、单并发，并要求显式授权运行时安装。
- Canonical PageIR Adapter 和 state schema v0.2；raw artifact 按源 hash、provider/model、adapter 和 PageIR schema 身份校验。
- SectionIR：按 SectionMap 候选范围重建，保留共享边界和 block accounting。
- KeywordIR：Description、Purpose、Options、Card 区域、summary/definition 表、合并行与点号子卡、固定字段槽位、条件、Variable Description 行/值表/文本前缀、变量族映射、Remarks、References 和 fallback block。
- 首版 Markdown renderer、Corpus manifest 和质量报告。
- PDF 文本层与 PageIR 的确定性抽样比对。
- 按文档和章节长度分层的可复现语义抽样，支持固定 seed 和低频结构显式 anchor。
- Theory Corpus v0.1 输出合同：章节级身份、统一 manifest、父子关系、保守 block 流、共享边界和状态语义已冻结。
- TheoryIR、Theory Markdown renderer、数字章节稳定路径、父子/兄弟 title-anchor block 所有权和 Keyword/Theory 统一 manifest 已实现。
- `build` 已改为一键执行 `inspect -> parse -> reconstruct`，支持 raw/PageIR checkpoint 复用、配额暂停和同命令恢复。
- 当前自动化测试共 134 个，全部通过。

### 当前回归状态

当前 R17 语义回归使用固定 seed `20260817`，manifest 为：

```text
workspace/regression/r17/semantic-sample/sample_manifest.json
```

其中包含 43 个抽样章节、258 个章节页引用和 224 个去重后的唯一页面。本地 OCR 会话于 2026-08-18 完成；随后可从已缓存 raw artifact 离线重建 PageIR，不再启动 Provider。当前为 224/224 完成、失败为 0。

独立 holdout 已完成本地 Paddle 解析和 v0.2 PageIR 重建：

```text
workspace/regression/r17/semantic-sample-holdout-20260818/sample_manifest.json
```

holdout 使用新 seed `20260818`、不追加旧 anchor，共 40 个章节、214 个章节页引用和 211 个去重页面；其中 195 个页面不在旧样本集合内，旧样本重叠率为 7.58%。211/211 页已生成 PageIR v0.2，40/40 个章节完整覆盖，`partial == 0`、`not_parsed == 0`。

holdout 最终检测结果：29/29 个 Keyword 样本通过 block accounting，23 个 `checked`、6 个 `warning`；共 103 个 Card、824 个 Card field、324 个 Variable Description、29 个变量族映射和 3 个续表。10/10 个 Theory 样本均为 `checked`，并生成 `THEORY_BOUNDARY_RESOLVED`；不再有 Theory 共享边界 warning。质量探针剩余 3 个 Source Material fallback、2 个未匹配变量标题和 2 个非等价 summary/definition 候选。文本层 16 个低 raw recall 页面均属于公式表示差异；非公式正文 recall 最低为 0.9297，没有普通 `TEXT_LAYER_DIVERGENCE`。

| 文档 | 唯一样本页 | PageIR `done` | 仅 raw | 未开始 |
| --- | ---: | ---: | ---: | ---: |
| Volume I | 79 | 79 | 0 | 0 |
| Volume II | 62 | 62 | 0 | 0 |
| Volume III | 47 | 47 | 0 | 0 |
| Theory | 36 | 36 | 0 | 0 |
| **合计** | **224** | **224** | **0** | **0** |

共享的 `workspace/run_r17/parsing/state.json` 另有 Volume II 第 32、33 页的两个较早远程超时记录。它们不属于上表的当前分层样本统计，恢复时应作为历史重试候选处理，不要与本轮样本完成率混淆。

当前 224 个样本页都已经生成并通过本地 checkpoint 身份校验。共享 state 中的两个历史远程超时记录不影响本轮样本 224/224 的完成率。

第一次主线回归已于 2026-08-18 完成：

- `sample-regression` 检查了全部 43 个章节和 258 个章节页引用，`partial == 0`、`not_parsed == 0`；
- 32 个 Keyword 样本中 8 个为 `checked`、24 个为 `warning`，另有 1 个 document 样本和 10 个 Theory 样本完成 PageIR 检查；
- 32/32 个 Keyword 样本均通过 block accounting；共重建 109 个 Card、791 个 Card field、15 个 Card condition、225 个 Variable Description、6 个变量族映射和 2 个续表；
- 质量探针检测到 27 个 Source Material fallback 候选、17 个字面量 `\\n` 候选、10 个 Card summary/definition 双重渲染候选和 5 个重复 Variable Description 候选；
- 116 个文本层抽样比较中有 10 个低于 `0.65` recall 门限，其中 Volume II 为 2 个、Theory 为 8 个；
- 全量 `reconstruct` 扫描了 1751 个 Keyword 条目并生成 79 个 Markdown，其中 11 个 `success`、68 个 `warning`、1672 个 `failed`。失败主要来自当前仅有样本 PageIR：报告包含 8139 个 `SECTION_PAGEIR_MISSING`，不能解释为已解析样本失败。

本轮检测报告位于 `workspace/regression/r17/semantic-sample/sample_detection.json`。后续重复回归必须保持相同 seed 和 anchor，且不得重新提交已经完成的样本 OCR。

在第一次主线回归基础上，PageIR span、空白页分流、Theory 所有权和五类 Keyword
通用规则均已收敛。最终两组结果如下：

| 指标 | 固定样本 | 独立 holdout |
| --- | ---: | ---: |
| 全部章节覆盖 | 43/43 | 40/40 |
| Keyword `checked` / `warning` | 24 / 8 | 23 / 6 |
| Theory `checked` | 10/10 | 10/10 |
| Card / Card field | 115 / 864 | 103 / 824 |
| Variable Description | 373 | 324 |
| 变量族映射 / 续表 | 31 / 6 | 29 / 3 |
| Source Material fallback | 3 | 3 |
| 未匹配变量标题 | 3 | 2 |

两组 Keyword 均全部通过 block accounting，`partial == 0`、`not_parsed == 0`。
20/20 个 Theory 样本均生成 `THEORY_BOUNDARY_RESOLVED`，不再有 Theory 共享边界
warning。固定样本 10 个、holdout 16 个低 raw recall 页面全部归为
`TEXT_LAYER_FORMULA_REPRESENTATION_DIVERGENCE`；没有普通正文
`TEXT_LAYER_DIVERGENCE`。

## 真实 Markdown 已知问题

规则收敛后仍需保守处理：

- 既有收敛报告中的 90 个 `TABLE_STRUCTURE_UNCERTAIN` 来自 v0.1 PageIR 的 rowspan/colspan 或不等宽投影；PageIR v0.2 已实现逻辑单元 span、迁移读取、结构校验和确定性矩形投影。新 holdout 的 211 页包含 136 个 span 单元，未产生 `TABLE_STRUCTURE_UNCERTAIN` 或 span 校验错误；旧报告仍保留为 v0.1 基线，不回写历史计数；
- 两组样本合并去重后有 13 个 `KEYWORD_BOUNDARY_AMBIGUOUS` 事件缺少唯一标题锚点，仍保留共享页内容；
- 5 个未匹配变量标题为 `Instability`、`ICO2`、`RHO`、`YMAX` 和 `KBUFSR`，均不存在唯一 Card 目录映射或与目录存在冲突；
- 唯一 O/0 映射会保留原始表格文本，并记录 info 级 `VARIABLE_IDENTIFIER_CONFUSABLE_MATCH`，不得静默改写；
- 7 个条目的 17 个 Card 仍有非等价 summary/definition，summary 含独有字段或与 definition 存在标识符冲突，因此两者都保留；
- 6 个条目仍有 Source Material fallback，原始块没有唯一变量或语义区域归属。

这些边界已经人工裁决为当前最终处理：保守保留，不通过猜测静默改写 Manual 原文。

两轮样本的边界及最终裁决已经去重写入本地报告：

```text
workspace/regression/r17/manual-review-report-20260818-v2.md
```

报告包含 13 个 Keyword 共享边界事件、5 个变量标题冲突、7 个条目的 17 个非等价
Card、6 个 Source Material fallback 和 17 个实际 O/0 关联。Theory 父子/兄弟边界
和 4 个已确认的源 PDF 空白分隔页已由通用规则解决，不再列为人工边界。

## 下一轮验证顺序

1. 人工审查报告已完成，维持其中确认的保守边界策略。
2. Theory renderer、统一 manifest 和一键 `build` 已完成 focused 与合成端到端测试。
3. 下一阶段使用干净环境和完整 Manual 验收一键构建；该验收尚未执行，不计入当前已完成范围。

## 有意保留的范围限制

- 本地运行时可以安装配置的 Python/model/layout artifact 并启动 llama-server，但不安装 NVIDIA 驱动、CUDA/WSL，也不替用户选择 llama-server 二进制来源。
- 本地 batch size 和并发保持 1，直到代表性速度和显存测试支持调整。
- `build` 已是完整主入口；独立 `inspect`、`parse`、`reconstruct` 命令继续用于诊断和开发。
- `workspace/` 下的 PDF 派生物、raw OCR、PageIR 和报告只是本地证据，不是可分发的仓库资产。

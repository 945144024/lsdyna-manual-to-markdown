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
- 当前自动化测试共 108 个，全部通过。

### 当前回归状态

当前 R17 语义回归使用固定 seed `20260817`，manifest 为：

```text
workspace/regression/r17/semantic-sample/sample_manifest.json
```

其中包含 43 个抽样章节、258 个章节页引用和 224 个去重后的唯一页面。本地 OCR 会话于 2026-08-18 完成 artifact 转换并退出。最终稳定 checkpoint 如下：

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

在第一次主线回归基础上完成通用规则收敛并使用相同样本复跑，结果如下：

| 指标 | 第一次回归 | 规则收敛后 |
| --- | ---: | ---: |
| Keyword `checked` / `warning` | 8 / 24 | 10 / 22 |
| Card / Card field | 109 / 791 | 114 / 856 |
| Variable Description | 225 | 367 |
| 变量族映射 / 续表 | 6 / 2 | 26 / 6 |
| 未分类 block | 119 | 7 |
| `VARIABLE_DESCRIPTION_UNMATCHED_TITLE` | 205 | 3 |
| `VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN` | 10 | 0 |
| 字面量 `\\n` 候选 | 17 | 0 |
| summary/definition 双重渲染候选 | 10 | 3 |
| 重复 Variable Description 候选 | 5 | 0 |
| Source Material fallback 候选 | 27 | 4 |

32/32 个 Keyword 样本仍通过 block accounting，43/43 个章节和 258/258 个章节页引用仍完整覆盖。10 个低 raw recall 页面均被公式感知验证归为 `TEXT_LAYER_FORMULA_REPRESENTATION_DIVERGENCE`；116 个比较页的非公式正文 recall 最低为 `0.891`，没有页面产生普通 `TEXT_LAYER_DIVERGENCE`。

## 真实 Markdown 已知问题

规则收敛后仍需保守处理：

- 90 个 `TABLE_STRUCTURE_UNCERTAIN` 来自 rowspan/colspan 或不等宽投影；Canonical PageIR v0.1 不保存 span，不能通过 renderer 猜测消除；
- 8 个 `KEYWORD_BOUNDARY_AMBIGUOUS` 缺少唯一标题锚点，仍保留共享页内容；
- 3 个未匹配变量标题为 `Instability`、`ICO2` 和 `RHO`，均不存在唯一 Card 目录映射；
- 唯一 O/0 映射会保留原始表格文本，并记录 `VARIABLE_IDENTIFIER_CONFUSABLE_MATCH`，不得静默改写；
- 3 个样本仍有非等价 Card summary/definition，summary 含独有字段或与 definition 存在标识符冲突，因此两者都保留；
- 4 个样本仍有 Source Material fallback，原始块没有唯一变量或语义区域归属。

这些问题应通过可复现的确定性 renderer/normalization 规则处理，不能静默改写 Manual 原文。

## 下一轮验证顺序

1. 人工核对剩余 3 个变量标题、3 个双表样本和 4 个 fallback，只有新增确定性证据时才扩展规则。
2. 若要消除 rowspan/colspan 不确定性，先设计带迁移路径的 PageIR span 扩展；不得破坏 v0.1 页面身份和现有基线。
3. 扩大到新的固定 seed 或下一 release 语义样本，验证当前规则不是对 R17 样本过拟合。
4. Keyword 回归稳定后，决定并实现 Theory Corpus 输出契约。

## 有意保留的范围限制

- 当前最终 Corpus 只生成 Keyword 章节；Theory 的 PageIR / SectionIR 支持不等于 Theory Markdown 已完成。
- 本地运行时可以安装配置的 Python/model/layout artifact 并启动 llama-server，但不安装 NVIDIA 驱动、CUDA/WSL，也不替用户选择 llama-server 二进制来源。
- 本地 batch size 和并发保持 1，直到代表性速度和显存测试支持调整。
- 兼容命令 `build` 仍是 ingest-only；已验证的开发主流程是 `inspect` -> `parse` -> `reconstruct`。
- `workspace/` 下的 PDF 派生物、raw OCR、PageIR 和报告只是本地证据，不是可分发的仓库资产。

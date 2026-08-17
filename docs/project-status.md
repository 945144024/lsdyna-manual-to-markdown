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
- KeywordIR：Purpose、Options、Card 区域、Card summary/definition 表、固定字段槽位、条件、Variable Description 行、变量族映射、Remarks、References 和 fallback block。
- 首版 Markdown renderer、Corpus manifest 和质量报告。
- PDF 文本层与 PageIR 的确定性抽样比对。
- 按文档和章节长度分层的可复现语义抽样，支持固定 seed 和低频结构显式 anchor。
- 当前自动化测试共 101 个，全部通过。

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

当前 224 个样本页都已经生成并通过本地 checkpoint 身份校验。下一轮不需要再次运行 OCR；应直接执行 reconstruction，并用相同 seed 和 anchor 重新生成 `sample_detection.json`。共享 state 中的两个历史远程超时记录不影响本轮样本 224/224 的完成率。

## 真实 Markdown 已知问题

此前 EOS/MAT 检查及 anchor 检查发现：

- Card summary 与 Card definition 在视觉上可能重复，即使 block accounting 正确；
- 常见 OCR 混淆包括 `EO`/`E0` 和 `VO`/`V0`；
- 部分表格单元格保留了字面量 `\\n`，而不是渲染为换行；
- OPT 内容中存在两个高度相似的表格片段；
- 一些无法可靠归类的原文仍位于明确标记的 `Source Material` fallback 中。

这些问题应通过可复现的确定性 renderer/normalization 规则处理，不能静默改写 Manual 原文。

## 下一轮验证顺序

1. 对已经完成的 224 个样本页运行 `reconstruct`。
2. 重新运行 `sample-regression` 并检查 `sample_detection.json`。
3. 按文档和长度分层比较完成率、issue 数、block accounting、Card/condition/variable 覆盖率和文本层 recall。
4. 从四类源文档的短、中、长章节中检查代表性 Markdown。
5. 针对可复现问题增加窄范围 renderer 修复和测试，再重复抽样。

## 有意保留的范围限制

- 当前最终 Corpus 只生成 Keyword 章节；Theory 的 PageIR / SectionIR 支持不等于 Theory Markdown 已完成。
- 本地运行时可以安装配置的 Python/model/layout artifact 并启动 llama-server，但不安装 NVIDIA 驱动、CUDA/WSL，也不替用户选择 llama-server 二进制来源。
- 本地 batch size 和并发保持 1，直到代表性速度和显存测试支持调整。
- 兼容命令 `build` 仍是 ingest-only；已验证的开发主流程是 `inspect` -> `parse` -> `reconstruct`。
- `workspace/` 下的 PDF 派生物、raw OCR、PageIR 和报告只是本地证据，不是可分发的仓库资产。

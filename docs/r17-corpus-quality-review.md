# R17 Corpus 质量审查

本文记录 2026-08-20 完整 R17 Corpus 的剩余高影响问题、变量标题分层审查和
自动收敛边界。审查对象为同一次一键构建的 2,333 个条目和 8,186 个 PageIR；
结论只使用 PDF 页面、PageIR、Card 目录和最终 Markdown 中可重复验证的证据。

本文是当前 R17 release candidate 的最终质量裁决，不是待自动清零的问题清单。
后续只有在新模型或新 release 提供新的唯一结构证据时才重新开启相关规则。

## 验收结论

- PageIR 覆盖为 8,186/8,186，页面失败和缺失均为 0。
- Manifest 包含 2,333 个条目：2,027 `success`、306 `warning`、0 `failed`。
- 2,333 个 Manifest Markdown 路径与 2,333 个非空文件一一对应。
- 4,939 个 issue 包含 4,188 `info`、750 `warning` 和 1 `error`。
- 当前结果可作为后续索引的开发验收 Corpus。`warning` 条目可被索引，但其
  review metadata 必须与正文共同保留；结构化字段不能脱离 issue 作为权威事实。

精确统计、Manifest 摘要、全部 Markdown 摘要和关键证据断言已冻结在
`docs/r17-corpus-acceptance-v0.1.json`。配置启用该文件后，`build` 会生成
`reports/acceptance.json`；任何覆盖、计数、路径或内容漂移都会使质量门失败。

## 高影响残留

| 位置 | 条目 | 结论 |
| --- | --- | --- |
| Vol. I PDF 721 | `BOUNDARY_FLUIDM_INTERIOR` | 模型把 Card 的 Variable/Type/Default/Remarks 行压入单行单元；Card 名和变量仍保留，缺少无歧义拆行坐标。保持 warning。 |
| Vol. I PDF 1590 | `CONTROL_FORMING_USER` | 两张相邻 Card 被投影为组合单元，Markdown 同时保留识别结果；无法只凭字符串确定全部行边界。保持 warning。 |
| Vol. I PDF 3392 | `LOAD_BODY_POROUS` | Card 1/2 被组合投影，第二张 Card 的变量 `AOPT` 可见但行标签不完整；不猜测拆分。保持 warning。 |
| Vol. II PDF 629 | `MAT_CONCRETE_DAMAGE_REL3` | 多张 Variable/Value Card 在同一复杂表内，部分变量含数学下标和数值；不能把 Value 行统一重标为 Variable。保持 warning。 |
| Vol. I PDF 2851 | `FREQUENCY_DOMAIN_RANDOM_VIBRATION` | 页面是 Variable Description 与 S-N Card 过渡，模型生成的局部 Card slot 缺号；已有正文被保留，缺少确定补位证据。保持 warning。 |
| Vol. I PDF 3336 | `ISPG_CONTROL_ADAPTIVITY` | 源 Card 2 有 8 个槽位但 PageIR 将 `IMERGE` 并入 `Variable Type` 单元，slot header 只识别 1、2；不推断第三槽。保持 warning。 |
| Vol. I PDF 3002 | 两个 `INCLUDE_COMPENSATION_*` 条目 | token-byte transport recovery 可审计，但 native parser 在拒绝字符处截断，不能证明缺失字符。保留 `MODEL_OUTPUT_BYTE_RECOVERY`。 |
| Vol. I PDF 3216 | `INTEGRATION_BEAM` | 非矩形编号网格属于 Figure 29-17/29-18 的积分点示意，不是可安全矩形化的 Card。保留唯一 error `TABLE_STRUCTURE_UNCERTAIN`。 |

这些案例均没有足以支持正文改写的唯一程序证据。本轮未为降低 warning 数量新增
特例或模糊规则。

## 未匹配变量标题分层

`VARIABLE_DESCRIPTION_UNMATCHED_TITLE` 共 566 个 issue，涉及 208 个 Keyword
条目。按原始标题形态分层后的完整统计如下；条目数在各层之间可重叠，因为同一条目
可能出现多种形态。

| 分层 | issue | 条目 | 代表性样本 | 判断 |
| --- | ---: | ---: | --- | --- |
| 变量式标识符 | 284 | 153 | `LFIT`、`ATMOSP`、`TYPE1`、`NGAS` | 形态像变量，但当前 Card catalog 无唯一同名目标；不能仅凭大写形式关联。 |
| 说明性/物理概念 | 106 | 11 | `volume`、`pressure`、`internal energy` | 属于输出量或说明概念，不是 Card slot 身份。 |
| 混合或 OCR 形态 | 82 | 36 | `Else`、`KijTYP`、`R00, R45, R90` | 可能是分组标题、公式或合并文本，缺少统一映射。 |
| 索引/符号族 | 52 | 28 | `ALOi`、`Xk`、`Wk`、`SIGij` | 只有具体 Card slot 集合唯一且无冲突时才能展开；当前样本不满足。 |
| 标记或输入示例 | 42 | 6 | `$ option &`、`* INCLUDE TRANSFORM`、`dummy.k` | 来源是输入示例/格式文本，不应关联到 Card 变量。 |

每层按文档、条目和首次出现页做了去重抽样。抽样没有发现一个可推广到全层、同时由
唯一 Card catalog 目标证明的规则，因此 566 个 issue 全部保留。现有的精确名称、
明确变量族以及唯一 O/0 关联规则继续有效；不得扩展为编辑距离或 OCR 拼写修正。

## Continuation orphan

28 个 `VARIABLE_DESCRIPTION_CONTINUATION_ORPHAN` issue 涉及 25 个条目，分布于三卷，
不存在单一页面版式。它们包含跨页续表、组合表和示例输入残片；没有匹配标题时，续行
不能可靠归给前一变量。当前实现保留原 block 与 warning，不自动向前吸附。

## 自动收敛准则

只有同时满足以下条件时才继续新增通用规则：

1. 目标身份来自同一条目的 Card catalog 或唯一强标题锚点；
2. 候选目标唯一，不与现有变量或边界冲突；
3. 规则不改写来源拼写、不补字符、不借用 PDF 文本层覆盖模型输出；
4. 真实页面和 focused test 均证明 block accounting、provenance 与重复构建稳定。

不满足这些条件的残留应保持 warning/error。后续开发评价规则质量时，以内容和证据
是否更可靠为目标，不以 warning 总数单调下降为目标。

原生 Windows 的 419 页跨平台样本未产生新增平台特有 issue，且逐页 PageIR 与 WSL
基线全等；因此本审查结论同时适用于当前 Windows 抽样验收范围。

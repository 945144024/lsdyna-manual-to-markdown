# 项目开发状态

本文记录 `0.1.0-dev` 的当前实现、验证证据、质量边界和后续工作。用户安装与运行
方式见根目录 `README.md`。

## 1. 当前定位

项目已经形成可运行的一键 Corpus 构建流水线：

```text
Manual PDF
  -> discovery / ingest
  -> PageMap + SectionMap
  -> page-centric ParsePlan
  -> PaddleOCR-VL raw artifact
  -> Canonical PageIR v0.2
  -> SectionIR
  -> KeywordIR / TheoryIR
  -> Markdown + manifest + reports
```

当前最终产物是可供后续索引的 Markdown Corpus，不包含向量化、RAG、MCP、知识图谱、
`.k` 文件解析或 Keyword 校验器。

## 2. 已实现能力

- Keyword Volume I-III 与 Theory Manual 的发现、身份解析和 ingest；
- PageMap / SectionMap v0.1、JSON Schema、TOC/页脚证据和 inspection 质量门；
- 页面级 ParsePlan、单页身份、transport batch 和断点续跑；
- PaddleOCR-VL remote Provider，以及本地 PaddleOCR worker + `llama-server`
  Provider；
- raw artifact、checkpoint 和 PageIR 的源文件/provider/model/adapter/schema 身份
  校验；
- Canonical PageIR v0.2，包含 typed block、bbox、issue 和表格
  `rowspan`/`colspan`；
- SectionIR block accounting、共享边界保留和页眉页脚 provenance；
- KeywordIR 的 Description、Purpose、Options、Card、条件、Variable Description、
  变量族、Remarks、References 和 Source Material fallback；
- TheoryIR 的数字章节身份、父子关系、title-anchor block 所有权和统一 manifest；
- Markdown renderer、`corpus.yaml`、`manifest.jsonl` 和统一质量报告；
- PDF 文本层抽样验证与公式表示差异分流；
- 冻结样本 manifest、分层语义回归和完整 Corpus quality gate；
- `build` 一键执行 `inspect -> parse -> reconstruct`，支持配额暂停和同命令恢复。

## 3. 版本支持范围

确定性 Inspection 回归基线覆盖 22 份文档：

- R12-R17 Keyword Manual，共 18 份；
- R14-R17 Theory Manual，共 4 份；
- 每个 release 支持任意非空 Keyword/Theory 文档组合。

R12-R16 的承诺止于 Inspection。完整 PageIR、Keyword/Theory 重建和 Corpus 验收目前
以 R17 为准。其他 release 可以 best-effort 运行，但不属于已验证范围。

## 4. R17 完整构建验收

2026-08-20 完成 Keyword Volume I-III 与 Theory 四册的一键构建验收。

| 指标 | 结果 |
| --- | ---: |
| ParsePlan 唯一页面 | 8,186 |
| PageIR 完成 / 失败 / 缺失 | 8,186 / 0 / 0 |
| Corpus 条目 | 2,333 |
| 条目 success / warning / failed | 2,027 / 306 / 0 |
| 非空 Markdown | 2,333 |
| issue 总数 | 4,939 |
| issue info / warning / error | 4,188 / 750 / 1 |

Manifest 与 Markdown 路径一一对应，没有空文件、重复路径或未列出的 Markdown。
Theory 父子关系完整且无环。最终整体状态为 `warning`，因为仍有需保留 review
metadata 的结构边界；这不等同于页面解析失败。

精确统计、manifest/Markdown 摘要和关键证据断言冻结在
`docs/r17-corpus-acceptance-v0.1.json`。配置
`quality_gate.baseline` 后，构建生成 `reports/acceptance.json`；任何覆盖率、
计数、路径、内容摘要或关键 evidence 漂移都会使质量门失败。

高影响残留及不继续自动收敛的理由见 `docs/r17-corpus-quality-review.md`。

## 5. 语义回归

固定分层样本使用 seed `20260817`：

- 43 个章节；
- 258 个章节页引用；
- 224 个唯一页面；
- PageIR 完成 224/224。

独立 holdout 使用 seed `20260818`：

- 40 个章节；
- 214 个章节页引用；
- 211 个唯一页面；
- PageIR 完成 211/211；
- 其中 195 页不在固定样本中。

两组 Keyword 样本均通过 block accounting；20/20 个 Theory 样本完成确定性边界
归属。低 raw recall 页面均被归为公式表示差异，没有普通正文
`TEXT_LAYER_DIVERGENCE`。

已经人工审阅或包含 pinned section 的集合以冻结 manifest 为唯一选择合同。复查时
必须使用 `sample-regression --sample-manifest`，不能依赖 seed 重新生成选择。

## 6. Windows 与 Linux/WSL

完整 8,186 页 R17 构建在 Linux/WSL 环境完成。原生 Windows 验收包括：

- PowerShell clean clone、CPython 3.12、pip/uv 安装路径和三个 CLI entry point；
- Windows Poppler 对四册 R17 的 Inspection，结果与 Linux/WSL 基线一致；
- 本地 PaddlePaddle GPU 3.2.1、PaddleOCR 3.7.0、RTX 4060 和
  `llama-server` build 10456；
- 4 个复杂结构哨兵页面；
- 固定样本 224 页和独立 holdout 211 页。

两组 Windows 样本去重后为 419 页，占 8,186 页的 5.1185%。它们包含 6,031 个
block、600 个 TableBlock、285 个 span 单元、586 个公式文本和 48 个 Figure；
解析 JSON 后逐页与 WSL PageIR 全等。Windows 没有重新推理其余 7,767 页，因此
不能把抽样验收描述为 Windows 完整 R17 构建。

跨平台修复包括：

- Poppler stdout 固定按 UTF-8 解码；
- Windows 原生路径不调用 `wslpath`；
- Windows 默认识别 `llama-server.exe` 和 `venv/Scripts/python.exe`；
- manifest 的 `markdown_path` 固定使用 POSIX `/`；
- Paddle worker HOME 和缓存隔离到 runtime 目录。

## 7. 安装与包元数据

源码仓库支持标准 `python -m venv` 与 `python -m pip install -e .`，不依赖
`uv`。全新标准 venv 中的 pip 安装和三个 console entry point 已验证。

`pyproject.toml` 使用 setuptools build backend、SPDX `MIT` 许可证表达式、
`LICENSE` 文件声明和公开仓库 URL。sdist 与 wheel 可以成功构建。当前公开测试版
仍以 clone 仓库为安装合同，不承诺从 PyPI 直接安装，也不要求 sdist/wheel 携带
仓库根目录的 `configs/` 和完整 `docs/`。

## 8. 自动化验证

当前自动化测试共 257 个，全部通过。覆盖范围包括：

- discovery、Inspection、PageMap / SectionMap 与质量门；
- ParsePlan、checkpoint、raw cache 和 Provider 错误处理；
- PageIR v0.2、table span、reading order 和 Adapter；
- KeywordIR/TheoryIR、renderer、manifest 和整体 build；
- Windows/Linux 路径、Poppler UTF-8 和本地 Provider runtime；
- 固定样本 manifest 复查与 Markdown 路径稳定性；
- 凭证清理、signed URL 脱敏和合成输入端到端测试。

发布前还应在 CI 中使用 Windows 与 Ubuntu、Python 3.10-3.12 执行 pip 安装、测试、
`compileall` 和包构建。真实 Manual 与模型文件不进入 CI 或公开仓库。

## 9. 已知质量边界

- 306 个 R17 warning 条目可读取和索引，但必须保留对应 issue metadata；
- 566 个未匹配变量标题缺少唯一 Card catalog 目标，不使用编辑距离或 OCR 猜测关联；
- 28 个 Variable Description continuation orphan 缺少统一版式和唯一归属；
- PDF 第 3002 页使用可审计 token-byte recovery，不能证明被 native parser 截断的
  字符；
- PDF 第 3216 页的非矩形网格属于 Figure，保留唯一
  `TABLE_STRUCTURE_UNCERTAIN` error，不强制解释为 Card；
- 本地 Provider 保持单页、单并发，不自动安装 NVIDIA 驱动/CUDA，也不替用户选择
  第三方 `llama-server` 二进制。

规则扩展必须基于真实页面中的唯一、可重复程序证据。不得为了降低 warning 数量而
引入手册特例、模糊匹配、上下文补字或文本层覆盖。

## 10. 发布前剩余工作

1. 固化并提交当前 Windows、pip、文档和仓库安全改动；
2. 增加 Windows/Ubuntu CI 和公开测试版发布说明；
3. 将版本元数据更新为公开测试版版本并创建 release tag；
4. 在新 Manual release 或新模型输出出现后采集独立样本，只在出现新结构证据时
   扩展通用规则。

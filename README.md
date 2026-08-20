# lsdyna-manual-builder

`lsdyna-manual-builder` 将用户自行提供的 LS-DYNA Keyword Manual 和 Theory Manual 转换为结构化、可追溯、适合大模型读取的 Markdown Corpus。项目不附带或分发任何 LS-DYNA 手册。

## 支持范围

以下范围已完成 discovery / PageMap / SectionMap 的真实 PDF 回归验证：

- R12、R13、R14、R15、R16、R17 Keyword Manual；
- R14、R15、R16、R17 Theory Manual；
- 同一 release 下任意非空组合，包括单册、部分 Keyword 卷、Keyword + Theory 和 Theory-only。

这项版本承诺对 R12-R16 只覆盖确定性 Inspection；R17 还完成了固定分层样本、独立 holdout 以及四册完整一键构建验收。完整 R17 验收生成了 2,333 个 Keyword/Theory 条目，其中 2,027 个为 `success`、306 个为 `warning`、0 个为 `failed`；8,186 个计划页面均已生成 PageIR。该结果证明完整流水线可以运行和恢复，不代表全部语义条目已经人工逐项核准。其他 release 允许运行，并标记为 `best-effort`，但不保证 PageMap、SectionMap 或后续解析结果。页面使用 `(document_id, pdf_page)` 作为稳定身份，`document_id` 为 `keyword-volume-1`、`keyword-volume-2`、`keyword-volume-3` 或 `theory`。

## 当前能力

项目当前处于 `0.1.0-dev`。这是一个可运行的开发版，Inspection、页面解析以及 Keyword/Theory 重建已经落地，但还不是“任意手册都能无人工复核”的生产质量保证：

- PageMap / SectionMap v0.1 契约已冻结；
- R12-R17 共 22 份 Manual 的严格回归基线已建立；
- 88 个抽样页面已完成 PageMap / SectionMap 模型视觉复核；
- PaddleOCR-VL Provider、raw artifact、PageIR v0.2 Adapter（含 rowspan / colspan）和页面级断点续跑基础设施已实现；
- `lsdyna-manual inspect` 可生成并校验 PageMap / SectionMap；
- `lsdyna-manual parse` 按 SectionMap 软边界与固定页面硬上限提交 PaddleOCR，显示页面进度并支持配额暂停与断点续跑；
- `lsdyna-manual reconstruct` 将现有 PageIR 聚合为 SectionIR，再生成 KeywordIR/TheoryIR，并输出可追溯的 Keyword/Theory Markdown、统一 manifest 与质量报告；未知 Keyword 结构会保留为 Source Material；
- Theory Corpus v0.1 合同、数字层级、title-anchor block 所有权、Markdown renderer 和统一 manifest 接入均已实现，并通过 R17 固定样本和 holdout 验证；
- `lsdyna-manual build` 是一键入口，依次执行 `inspect -> parse -> reconstruct`；解析阶段沿用 raw/PageIR checkpoint，配额暂停后可用相同命令恢复；
- `corpus.yaml` 和 `reports/summary.json` 同时报告条目状态、页面解析覆盖率和 issue 聚合；`reports/issues.jsonl` 汇总 Inspection、Parsing、Reconstruction/PageIR 与文本层验证问题，并保留页级来源；
- KeywordIR 已识别 Description、Purpose、Option、Card、变量说明区域、Remarks 和 References；Card 表支持 summary / definition、合并行、点号子卡、固定槽位和 summary 缺槽补充；Variable Description 支持表格行、跨块文本、值表、续表、显式列表和变量族归属。renderer 会保守处理 O/0 歧义、字面单元格换行和精确重复片段；文本层验证同时报告 raw visual recall 与非公式正文 recall。

```text
Manual PDF
  -> discovery / ingest
  -> deterministic inspection
  -> PageMap + SectionMap
  -> page-centric ParsePlan
  -> PaddleOCR-VL raw artifacts
  -> Canonical PageIR
  -> SectionIR + block-level KeywordIR / TheoryIR
  -> CardIR fields + Keyword variable catalog / Theory ownership
  -> Conservative semantic Markdown Corpus + reports
```

## 安装

运行环境需要 Python 3.10 或更高版本。`inspect` 还需要 Poppler 的 `pdftotext`。

```bash
# Debian / Ubuntu
sudo apt-get install poppler-utils

python -m venv .venv
. .venv/bin/activate
pip install -e .
```

开发环境可安装测试依赖：

```bash
pip install -e ".[dev]"
```

## 准备 Manual

从 LS-DYNA 官方渠道取得 Manual，并保持官方文件名放入 `manuals/`。项目不负责下载、授权或分发 Manual。

```text
LS-DYNA_Manual_Volume_I_R13.pdf
LS-DYNA_Manual_Vol_II_R17.pdf
LS-DYNA_Manual_Theory_R17.pdf
```

同一目录可以保存多个 release。配置中的 `manual.release` 决定本次运行选择的版本。

## 配置

复制示例为本地配置：

```bash
cp configs/example.yaml configs/local.yaml
```

`configs/local*.yaml`、`configs/*.local.yaml` 和 `configs/*.secret.yaml` 已被 Git 忽略。请只在这些本地配置中填写 API Key。

```yaml
manual:
  release: "R17"
  manuals_dir: "./manuals"

parser:
  provider: "paddleocr-vl-remote"
  model: "PaddleOCR-VL-1.6"
  job_url: "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
  api_key: "<your PaddleOCR API key>"
  max_batch_pages: 1

output:
  corpus_dir: "./workspace/run_r17"
```

API Key 从 [百度 AI Studio PaddleOCR](https://aistudio.baidu.com/paddleocr) 获取。`inspect` 不访问远程 API；`parse` 和一键 `build` 在存在未缓存页面时会创建 Provider，因此远程模式必须填写 API Key。若请求页面的 raw artifact 已全部通过身份校验，则可离线重建 PageIR，不启动 Provider。

本地模式将 `parser.provider` 改为 `paddleocr-vl-local`。本地 provider 由 PaddleOCR Python worker、PaddleOCR-VL 官方 GGUF、`llama-server` 和 PaddleX 版面模型组成，始终按单页调用，`max_batch_pages` 会被强制为 `1`。模型文件可以由运行时从配置的 Hugging Face 仓库下载；`llama-server` 本身必须已有可执行文件，或配置明确的 direct/archive 下载 URL，项目不会猜测二进制来源。
辅助版面模型默认从 PaddleX 官方 BOS 源获取，可通过 `parser.local.model_source` 改为
`huggingface`、`modelscope`、`aistudio` 或 `null`（继承当前环境）。
首次准备本地依赖、模型或辅助版面模型时，必须同时设置 `auto_prepare_runtime: true` 和运行命令的 `--allow-runtime-install`；默认只校验现有运行时。此开关不会安装 NVIDIA 驱动、CUDA/WSL，也不会替用户选择或验证第三方 `llama-server` 下载地址。

最小本地配置示例（路径和下载 URL 必须按实际环境填写）：

```yaml
parser:
  provider: "paddleocr-vl-local"
  model: "PaddleOCR-VL-1.6"
  max_batch_pages: 1
  local:
    runtime_dir: "./.runtime/paddleocr-local"
    llama_server_path: "/path/to/llama-server"
    # 也可不提供 path，改为配置 llama_server_download_url 或
    # llama_server_archive_url，并确保归档会解出上述预期路径。
    model_path: "/path/to/PaddleOCR-VL-1.6-GGUF.gguf"
    mmproj_path: "/path/to/PaddleOCR-VL-1.6-GGUF-mmproj.gguf"
    auto_prepare_runtime: false
    auto_start_server: true
    max_concurrency: 1
```

本地 worker 默认使用 PaddleX 的 OpenAI-compatible chat 路径。仅当配置的 `llama-server` 返回已知且结构完全匹配的 PEG-native 解析错误时，worker 才会对同一个识别 block 启动一次受限恢复：使用原始消息调用 `/apply-template`，校验 vision capability、图片 marker 和确定性生成参数，再从流式 `/completion` 的逐 token byte 证据重建输出。恢复必须收到连续且相互一致的 token 事件，并以已知 Content-only 解析错误终止；正常结束、断流、字段不符或任何其他错误都会保留原页面失败。程序不信任流中的文本投影，也不修正 OCR 字符。由于 native parser 可能在拒绝字符处提前终止，任何成功使用该路径的页面都会生成 `MODEL_OUTPUT_BYTE_RECOVERY` warning；解码输出保存在本地 raw transport metadata 中，无效 UTF-8 byte 使用 `U+FFFD`（`�`）保留。

若希望程序安装缺失的 PaddleOCR Python 依赖并下载已配置的运行时产物，将 `auto_prepare_runtime` 设为 `true`，且首次执行：

```bash
lsdyna-manual build configs/local.yaml --allow-runtime-install
```

准备完成后的正常运行不需要继续传 `--allow-runtime-install`。

非标准文件名通过 `manual.documents` 显式指定：

```yaml
manual:
  release: "R17"
  documents:
    - path: "manuals/custom-keyword.pdf"
      manual_type: "keyword"
      volume: 2
    - path: "manuals/custom-theory.pdf"
      manual_type: "theory"
```

一次运行中的文档必须属于同一 release，`documents` 不得为空。

## 运行

```bash
# 推荐入口：自动完成 inspect、可续跑 parse 和 reconstruct。
lsdyna-manual build configs/local.yaml

# 各阶段也可独立运行，用于诊断和开发。
lsdyna-manual inspect configs/local.yaml
lsdyna-manual parse configs/local.yaml
lsdyna-manual reconstruct configs/local.yaml

# 只解析一个文档中的前 50 个唯一页面，用于小批量验证。
lsdyna-manual parse configs/local.yaml \
  --document keyword-volume-2 \
  --max-pages 50

lsdyna-manual-regression \
  --manuals-dir manuals \
  --output-dir workspace/regression \
  --baseline docs/regression-baseline-v0.1.json \
  --require-reviewed

# R17 语义重建分层随机抽样；anchor 用于补充已知边界结构。
lsdyna-manual sample-regression \
  --manuals-dir manuals \
  --release R17 \
  --intermediate-dir workspace/regression/r17/intermediate \
  --pageir-dir workspace/run_r17/parsing/pageir \
  --output-dir workspace/regression/r17/semantic-sample \
  --seed 20260817 \
  --anchor keyword-volume-2:EOS_LINEAR_POLYNOMIAL \
  --anchor keyword-volume-2:EOS_JWL \
  --anchor keyword-volume-2:EOS_RATIO_OF_POLYNOMIALS
```

`inspect` 的产物位于 `<corpus_dir>/intermediate/`：

```text
intermediate/
├── keyword-volume-1/       # 仅输入时存在
│   ├── pagemap.json
│   ├── sectionmap.json
│   ├── toc_index.json
│   ├── legacy_alias_map.json
│   └── issues.jsonl
├── keyword-volume-2/
├── keyword-volume-3/
├── theory/
└── inspection_summary.json
```

`parse` 要求同一输出目录已经存在有效的 PageMap / SectionMap。主进度按唯一页面数计算，批次只是传输优化；远程和本地 Provider 当前都默认每批 1 页，以获得一致的页面映射与故障隔离。远程 Provider 仍可显式调高 `max_batch_pages`，并继续优先在 SectionMap 章节起点切分。解析产物位于 `<corpus_dir>/parsing/`，其中 `state.json` 同时保存页面 checkpoint 和已提交批次的远端 job ID。重新运行相同命令会先校验源 PDF、raw artifact 和 PageIR，再跳过有效页面。

本地 provider 由程序自动启动和停止 `llama-server`，并通过独立 worker 调用 PaddleOCR；它不依赖远程配额。模型推理速度受 GPU、llama-server 构建和页面复杂度影响，当前实现保持 `max_concurrency: 1`，尚未宣称本地并行或 batch 大于 1 已验证。

PaddleOCR 返回配额耗尽时，`parse` 停止提交后续批次、保留 checkpoint，并以退出码 `3` 返回。配额恢复或更换同 provider 的 API Key 后，直接重新运行原命令即可；程序不依赖配额重置日期。

`reconstruct` 要求已经存在 inspection 与 PageIR 产物。它严格保留 SectionMap 的候选页范围，缺页、空页或相邻章节共享边界页时不猜测内容归属，而是生成 `warning` / `failed` 状态和对应 issue。Keyword 正文做确定性语义结构化；Theory 按数字章节与唯一 title anchor 切分 block 所有权。两类记录写入同一个 `manifest.jsonl`，Theory 文件位于 `markdown/theory/`。默认还会对每个文档的首/中/尾 PageIR 与 PDF 文本层做抽样 token 比对；报告只用于验证，不覆盖 PageIR。产物为 `corpus.yaml`、`manifest.jsonl`、`markdown/` 和 `reports/`。

`reconstruct` 的退出状态反映整体质量：存在无法生成的条目或文档 ingest 失败时返回 failed；存在 warning 条目、解析失败/缺页或 warning/error issue 时返回 warning。条目状态和页面覆盖率是独立信号，因此 `status_failed: 0` 不表示所有计划页面都已成功解析。用户应先查看 `reports/summary.json` 中的 `parse_*`、`status_*` 和 issue 汇总，再按 `reports/issues.jsonl` 定位问题，然后将生成的 Markdown 作为下游数据使用。

`sample-regression` 从 Volume I、II、III 和 Theory 各自的 SectionMap 中按短章节（1～2 页）、中章节（3～6 页）、长章节（7～40 页）分层抽取，默认每个文档为 `3/4/3` 个样本。固定 `--seed` 会得到相同选择；`--anchor` 只追加已知边界结构，不改变随机层的选择。命令不会调用 OCR Provider，只扫描 PDF 文本层、读取已有 PageIR 并生成抽样 Markdown。输出 `sample_manifest.json` 保存章节身份、页范围、源 PDF hash、seed、选择理由和结构候选标记；`sample_detection.json` 保存 PageIR 覆盖率、Markdown 质量探针、文本层 recall 与 issue 分布。`not_parsed` 表示样本尚未经过 Provider，不等同于解析失败。

生成 manifest 后，可将它直接交给解析阶段。`--intermediate-dir` 用于指定同一批四份文档的 PageMap / SectionMap 目录；解析器会校验 manifest 中的 release 和源 PDF hash，只提交 manifest 的唯一页面集合，并继续使用现有 checkpoint：

```bash
lsdyna-manual parse configs/local.yaml \
  --intermediate-dir workspace/regression/r17/intermediate \
  --sample-manifest workspace/regression/r17/semantic-sample/sample_manifest.json
```

`build` 是面向用户的完整入口。它首先重新生成确定性的 inspection 导航产物，然后执行可续跑解析，最后生成完整 Corpus。若 Provider 报告配额耗尽，命令以退出码 `3` 暂停且不提前运行 reconstruction；恢复配额后重新执行同一命令即可。有效 raw/PageIR 会通过源文件、provider/model、adapter 和 schema 身份校验后复用。

发布或回归配置可选填 `quality_gate.baseline`。它在 reconstruction 后严格比较已审阅
基线的页面覆盖、条目/issue 分布、manifest 和全部 Markdown 内容摘要，并写出
`reports/acceptance.json`；不匹配时构建返回 failed。普通首次构建无需配置质量门，
因为基线只适用于创建它时使用的同一 release 和同一组 Manual 文件。R17 开发验收
基线见 `docs/r17-corpus-acceptance-v0.1.json`。

## 当前局限

- R12-R16 的版本验证覆盖确定性 Inspection；R17 已完成完整 Corpus 构建验收，但生成条目仍未全部经过人工语义复核；
- OCR 或视觉模型可能产生识别偏差、空结果和结构歧义。程序会重试、保留来源并输出 warning / failed 报告，但不保证任意输入都无需复核；
- 完整 R17 验收已生成全部 8,186 个 PageIR；其中 1 页使用受审计的 token-byte 恢复，相关条目保持 warning，缺失字符和被截断的模型文本不会按上下文补写；
- R17 报告仍保留 1 个 `TABLE_STRUCTURE_UNCERTAIN` error：对应 Figure 内的非矩形网格，程序不将其强制解释为 Card 表；
- 证据不足的共享边界、变量归属和非等价 Card 不会被猜测合并，可能在最终 Markdown 中保守双保留或进入 Source Material；
- 本地 Provider 当前只验证了单页、单并发；运行时准备不安装 NVIDIA 驱动、CUDA/WSL，也不自动选择 `llama-server` 二进制来源；
- 其他 release 可以 `best-effort` 运行，但不属于已验证版本范围。

## 凭证与版权安全

- `manuals/*`、所有 PDF、`workspace/*` 和本地配置均被 `.gitignore` 排除；
- API Key 使用 Pydantic `SecretStr` 加载，配置对象的字符串表示不会显示明文；
- API Key 不得写入日志、Corpus、报告、raw artifact、checkpoint 或回归基线；
- Provider 返回的 signed result URL 在持久化前会被清除；
- `docs/regression-baseline-v0.1.json` 只保存 hash、计数、覆盖率和审阅状态，不包含 Manual 原文或渲染页。

提交前仍应执行凭证扫描，并确认 `git status` 不包含 Manual、workspace 产物或本地配置。

## 文档

- [PageMap / SectionMap 契约](docs/pagemap-sectionmap.md)
- [Parser 与 Provider 架构](docs/parser-interface.md)
- [Corpus 格式](docs/corpus-format.md)
- [Theory Corpus 合同](docs/theory-corpus-contract.md)
- [Markdown 格式](docs/markdown-style.md)
- [当前开发状态与回归记录](docs/project-status.md)

## 开发验证

```bash
.venv/bin/pytest -q -s --basetemp=workspace/pytest-tmp
.venv/bin/python -m compileall -q src tests
git diff --check
```

## 许可证

项目采用 [MIT](LICENSE) 许可证。LS-DYNA Manual 的版权和使用许可不属于本项目许可证范围。

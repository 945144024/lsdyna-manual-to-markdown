# lsdyna-manual-builder

`lsdyna-manual-builder` 将用户自行提供的 LS-DYNA Keyword Manual 和 Theory Manual 转换为结构化、可追溯、适合大模型读取的 Markdown Corpus。项目不附带或分发任何 LS-DYNA 手册。

## 支持范围

项目经过真实 PDF 回归验证并承诺支持：

- R12、R13、R14、R15、R16、R17 Keyword Manual；
- R14、R15、R16、R17 Theory Manual；
- 同一 release 下任意非空组合，包括单册、部分 Keyword 卷、Keyword + Theory 和 Theory-only。

其他 release 允许运行，并标记为 `best-effort`，但不保证 PageMap、SectionMap 或后续解析结果。页面使用 `(document_id, pdf_page)` 作为稳定身份，`document_id` 为 `keyword-volume-1`、`keyword-volume-2`、`keyword-volume-3` 或 `theory`。

## 当前能力

项目当前处于 `0.1.0-dev`：

- PageMap / SectionMap v0.1 契约已冻结；
- R12-R17 共 22 份 Manual 的严格回归基线已建立；
- 88 个抽样页面已完成模型视觉复核；
- PaddleOCR-VL Provider、raw artifact、PageIR Adapter 和页面级断点续跑基础设施已实现；
- `lsdyna-manual inspect` 可生成并校验 PageMap / SectionMap；
- `lsdyna-manual build` 当前只执行发现、校验和 ingest，不调用远程 OCR，也不生成最终 Markdown；
- Section Reconstruction 和最终 Markdown Renderer 尚未实现。

```text
Manual PDF
  -> discovery / ingest
  -> deterministic inspection
  -> PageMap + SectionMap
  -> page-centric ParsePlan
  -> PaddleOCR-VL raw artifacts
  -> Canonical PageIR
  -> Section Reconstruction          (planned)
  -> Markdown Corpus                 (planned)
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

output:
  corpus_dir: "./workspace/run_r17"
```

API Key 从 [百度 AI Studio PaddleOCR](https://aistudio.baidu.com/paddleocr) 获取。当前 `inspect` 和 ingest-only `build` 不访问远程 API，因此 `api_key` 可暂时保持 `null`；实际创建 PaddleOCR Provider 时必须填写。

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
lsdyna-manual inspect configs/local.yaml
lsdyna-manual build configs/local.yaml

lsdyna-manual-regression \
  --manuals-dir manuals \
  --output-dir workspace/regression \
  --baseline docs/regression-baseline-v0.1.json \
  --require-reviewed
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

`build` 当前生成 `corpus.yaml`、空的 `manifest.jsonl`、`markdown/` 骨架和 `reports/`。报告会明确记录 page parsing 尚未接入 CLI。

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
- [Markdown 格式](docs/markdown-style.md)

## 开发验证

```bash
.venv/bin/pytest -q -s --basetemp=workspace/pytest-tmp
.venv/bin/python -m compileall -q src tests
git diff --check
```

## 许可证

项目采用 [MIT](LICENSE) 许可证。LS-DYNA Manual 的版权和使用许可不属于本项目许可证范围。

# lsdyna-manual-builder

`lsdyna-manual-builder` 是一个开源工具项目，用于将用户自行提供且合法获得的 LS-DYNA Keyword Manual 转换为结构化、来源可追溯、适合 LLM 读取的 Markdown Corpus。转换在用户本地完成。

## 版权与使用说明

- 本项目不提供、不分发、不内置任何版本的 LS-DYNA Manual（PDF 或其他形式的版权文本）；
- LS-DYNA Manual 可从官方手册下载页获取：https://lsdyna.ansys.com/manuals-download/ ，本项目与 ANSYS 无关联，不镜像、不分发手册文件；
- 用户需要自行准备合法获得的 LS-DYNA Manual 文件，存放于本地 `manuals/` 目录；
- 用户需要自行配置并提供文档解析所需的后端服务与 API 凭证；
- 仓库通过 `.gitignore` 排除 Manual PDF、API Key 与本地工作区数据，避免版权内容或密钥误入版本控制。

## 当前状态

项目处于早期开发阶段（v0.1.0-dev）。数据格式与接口规范见 `docs/`。

当前流水线：

```text
Manual PDF
  → Manual discovery / ingest
  → Document Inspection
  → PageMap + SectionMap
  → page-centric ParsePlan
  → Document Parser / Provider
  → provider-specific raw artifact
  → Provider Adapter
  → Canonical PageIR v0.1
  → [Reliable PageIR validation]       # pending
  → Section Reconstruction             # planned
  → Markdown Corpus                    # planned
```

当前已实现：

- Manual 卷发现：支持 `LS-DYNA_Manual_Volume_*_R*` 与 `LS-DYNA_Manual_Vol_*_R*` 两类官方文件名形态，可在同一目录中按 `manual.release` 选择目标版本；开发测试覆盖 R12 至 R17；
- `lsdyna-manual inspect`：确定性文档检查（PageMap / SectionMap）。检查不调用模型，产物写入 `output.corpus_dir/intermediate/`，覆盖 TOC 解析、页眉页脚识别、正文标题核验、无页脚区域搜索、重复印刷页码与 Unicode 兼容字符归一化等场景；
- Page-centric ParsePlan：从 SectionMap 候选页合并去重，生成以 `(volume, pdf_page)` 为语义单位的解析计划，并将连续页组成 transport batch；
- `paddleocr-vl-remote` Provider：提交任务、轮询、超时、重试、结果下载与 API Key 环境变量读取；
- Provider raw artifact persistence：保存 job JSONL、逐页 JSON 与 Provider Markdown，作为 provenance 与调试材料；
- page-level cache / resume：按唯一 `(volume, pdf_page)` 记录状态，区分 raw cache 与 PageIR cache；
- Paddle Adapter v0.1：将 Paddle raw 结构转换为 Canonical PageIR；
- Canonical PageIR v0.1 数据模型、序列化与基础校验。

尚未实现或尚待验证：

- 代表性真实页面的 Reliable PageIR 验证尚未完成；Canonical PageIR v0.1 仍是待验证 schema，不是最终冻结格式；
- page parsing 目前以 Python 模块形式提供，尚未暴露为稳定 CLI 命令；
- Section Reconstruction 尚未实现；
- 最终 Keyword Markdown Renderer 尚未实现；
- `lsdyna-manual build` 当前仍是 ingest-only，不会调用 page parsing，也不会生成最终 Keyword Markdown。

下一阶段将在 PageIR 通过真实页面验证后，实施 Section Reconstruction 与 Markdown Corpus 生成。

## 工作流

项目目标工作流如下。

### 输入

- Manual PDF：从 LS-DYNA 官方手册下载页 https://lsdyna.ansys.com/manuals-download/ 获取所选版本的 Keyword User's Manual（三卷），保持原始文件名放入本地 `manuals/` 目录；
- 配置文件：解析后端与转换参数，格式参见 `configs/example.yaml`。

### 输出

目标流水线完成后，在用户指定的本地目录生成标准化 Corpus：

- `corpus.yaml`：语料库级元数据，包含 Manual 版本、构建信息与统计；
- `manifest.jsonl`：逐条 Manual 条目的身份、来源、输出路径和转换状态索引，包括 Keyword、Volume、PDF 页面序号、Manual 印刷页码等信息；
- `markdown/`：按 `volume → family → keyword` 组织的标准化 Markdown 文档；
- `reports/`：转换过程报告与质量问题清单。

当前 `build` 只生成 Corpus 骨架和 ingest 报告，最终 Markdown Corpus 尚未生成。数据格式与输出规范见 `docs/`。

### 运行

```bash
pip install -e .
lsdyna-manual inspect configs/example.yaml   # 确定性文档检查（PageMap/SectionMap）
lsdyna-manual build configs/example.yaml     # 构建流水线（ingest 阶段）
```

`inspect` 依赖 `pdftotext`（poppler-utils），在解析前利用 TOC、页眉页脚与文本层建立页面导航图，产物写入 `output.corpus_dir/intermediate/`：

```text
intermediate/
├── volume-1/
│   ├── pagemap.json
│   ├── sectionmap.json
│   ├── toc_index.json
│   ├── legacy_alias_map.json
│   └── issues.jsonl
├── volume-2/
├── volume-3/
└── inspection_summary.json
```

`build` 按配置发现 `manuals/` 中指定 release 的 Manual，默认要求三卷齐全；可通过 `manual.require_all_volumes: false` 允许缺卷，或通过 `manual.volumes` 显式指定路径。完成文件名校验、sha256 与页数采集后，在 `output.corpus_dir` 写入 Corpus 骨架（`corpus.yaml`、空的 `manifest.jsonl`、`markdown/` 目录）与构建报告。当前 build 不调用 page parsing，输出为 0 条目并在报告中如实说明。

页面解析基础设施位于 `src/lsdyna_manual/parser/`，当前以 Python API 形式提供；稳定 CLI 入口将在 Reliable PageIR 验证完成前保持不暴露。解析过程生成的 raw artifact、PageIR 与 checkpoint 属于 workspace 本地中间产物，不属于最终 Corpus。

## 仓库结构

```text
lsdyna-manual-builder/
├── README.md               # 项目说明
├── LICENSE                 # MIT
├── pyproject.toml          # Python 项目与构建配置
├── .gitignore              # 排除 Manual、密钥与本地运行数据
├── docs/                   # 数据格式与接口规范
│   ├── corpus-format.md    # Corpus 输出结构规范
│   ├── markdown-style.md   # Keyword Markdown 格式规范
│   └── parser-interface.md # Parser 分层与 Provider 接口规范
├── src/lsdyna_manual/      # 源代码包
│   ├── parser/             # Inspection、ParsePlan、Document Parser、raw artifact 与 PageIR
│   │   ├── segmentation.py # PageMap / SectionMap
│   │   ├── page_ir.py      # Canonical PageIR v0.1
│   │   ├── parse_plan.py   # page-centric ParsePlan
│   │   ├── document_parser.py
│   │   ├── parse_state.py  # page-level cache / resume
│   │   ├── raw_store.py    # provider raw artifact persistence
│   │   └── adapters/       # Provider Adapter
│   ├── providers/          # 解析 API / 模型后端访问
│   ├── reconstruction/     # LS-DYNA 语义重建，尚未实现
│   ├── markdown/           # 最终 Keyword Markdown 生成，尚未实现
│   ├── manifest/           # Manifest 与 Corpus 元数据生成
│   ├── validation/         # 转换质量校验
│   └── cli/                # 命令行入口
├── configs/
│   └── example.yaml        # 配置文件模板
├── tests/                  # 单元测试
│   └── synthetic/          # 合成测试数据规范，不含官方 Manual 内容
├── manuals/                # 用户本地 Manual 存放目录，内容不进入版本控制
└── workspace/              # 本地运行与输出目录，不进入版本控制
```

## 许可证

本项目采用 [MIT](LICENSE) 许可证。

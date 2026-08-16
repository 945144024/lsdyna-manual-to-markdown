# lsdyna-manual-builder

`lsdyna-manual-builder` 是一个开源工具项目，用于将用户自行提供且合法获得的 LS-DYNA Keyword Manual 转换为结构化、来源可追溯、适合 LLM 读取的 Markdown Corpus。转换在用户本地完成。

## 版权与使用说明

- 本项目不提供、不分发、不内置任何版本的 LS-DYNA Manual（PDF 或其他形式的版权文本）；
- LS-DYNA Manual 可从官方手册下载页获取：https://lsdyna.ansys.com/manuals-download/ ，本项目与 ANSYS 无关联，不镜像、不分发手册文件；
- 用户需要自行准备合法获得的 LS-DYNA Manual 文件，存放于本地 `manuals/` 目录；
- 用户需要自行配置并提供文档解析所需的后端服务与 API 凭证；
- 仓库通过 `.gitignore` 排除 Manual PDF、API Key 与本地工作区数据，避免版权内容或密钥误入版本控制。

## 当前状态

项目处于早期开发阶段（v0.1.0-dev）。`lsdyna-manual build` 命令已提供配置加载、Manual 卷发现与元数据采集（sha256、页数）功能，并生成 Corpus 骨架与构建报告；PDF 解析与 Markdown 生成尚未实现，构建输出为 0 条目并如实报告。数据格式与接口规范见 `docs/`。

## 工作流

项目目标工作流如下。

### 输入

- Manual PDF：从 LS-DYNA 官方手册下载页 https://lsdyna.ansys.com/manuals-download/ 获取所选版本的 Keyword User's Manual（三卷），保持原始文件名放入本地 `manuals/` 目录；
- 配置文件：解析后端与转换参数，格式参见 `configs/example.yaml`。

### 输出

转换在用户指定的本地目录生成标准化 Corpus：

- `corpus.yaml`：语料库级元数据，包含 Manual 版本、构建信息与统计；
- `manifest.jsonl`：逐条 Manual 条目的身份、来源、输出路径和转换状态索引，包括 Keyword、Volume、PDF 页面序号、Manual 印刷页码等信息；
- `markdown/`：按 `volume → family → keyword` 组织的标准化 Markdown 文档；
- `reports/`：转换过程报告与质量问题清单。

数据格式与输出规范的完整定义见 `docs/`。

### 运行

```bash
pip install -e .
lsdyna-manual build configs/example.yaml
```

命令按配置发现 `manuals/` 中的三卷 Manual，完成文件名校验、sha256 与页数采集后，在 `output.corpus_dir` 写入 Corpus 骨架（`corpus.yaml`、空的 `manifest.jsonl`、`markdown/` 目录）与构建报告。当前版本 PDF 解析与 Markdown 生成尚未实现，构建输出为 0 条目并在报告中如实说明。

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
│   ├── parser/             # PDF/文档解析
│   ├── providers/          # 解析 API / 模型后端适配
│   ├── reconstruction/     # LS-DYNA 语义重建
│   ├── markdown/           # Markdown 生成
│   ├── manifest/           # Manifest 与 Corpus 元数据生成
│   ├── validation/         # 转换质量校验
│   └── cli/                # 命令行入口
├── configs/
│   └── example.yaml        # 配置文件模板
├── tests/
│   └── synthetic/          # 合成测试数据，不含官方 Manual 内容
├── manuals/                # 用户本地 Manual 存放目录，内容不进入版本控制
└── workspace/              # 本地运行与输出目录，不进入版本控制
```

## 许可证

本项目采用 [MIT](LICENSE) 许可证。

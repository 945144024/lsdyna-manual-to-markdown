# lsdyna-manual-builder

`lsdyna-manual-builder` 是一个开源工具项目，用于将用户自行提供且合法获得的 LS-DYNA Keyword Manual 转换为结构化、来源可追溯、适合 LLM 读取的 Markdown Corpus。转换在用户本地完成。

## 版权与使用说明

- 本项目不提供、不分发、不内置任何版本的 LS-DYNA Manual（PDF 或其他形式的版权文本）；
- 用户需要自行准备合法获得的 LS-DYNA Manual 文件，存放于本地 `manuals/` 目录；
- 用户需要自行配置并提供文档解析所需的后端服务与 API 凭证；
- 仓库通过 `.gitignore` 排除 Manual PDF、API Key 与本地工作区数据，避免版权内容或密钥误入版本控制。

## 当前状态

项目处于早期开发阶段（v0.1.0-dev）。当前仓库包含项目结构、数据格式规范与接口规范文档，尚未提供完整的自动化转换功能。

## 工作流

项目目标工作流如下。

### 输入

- Manual PDF：用户本地合法获取的 LS-DYNA Keyword Manual；
- 配置文件：解析后端与转换参数，格式参见 `configs/example.yaml`。

### 输出

转换在用户指定的本地目录生成标准化 Corpus：

- `corpus.yaml`：语料库级元数据，包含 Manual 版本、构建信息与统计；
- `manifest.jsonl`：逐条 Manual 条目的身份、来源、输出路径和转换状态索引，包括 Keyword、Volume、PDF 页面序号、Manual 印刷页码等信息；
- `markdown/`：按 `volume → family → keyword` 组织的标准化 Markdown 文档；
- `reports/`：转换过程报告与质量问题清单。

数据格式与输出规范的完整定义见 `docs/`。

## 仓库结构

```text
lsdyna-manual-builder/
├── README.md               # 项目说明
├── LICENSE                 # Apache-2.0
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

本项目采用 [Apache-2.0](LICENSE) 许可证。

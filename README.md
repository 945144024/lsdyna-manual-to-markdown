# lsdyna-manual-builder

`lsdyna-manual-builder` 是一个开源工具项目，用于将用户**自行提供且合法获得**的 LS-DYNA Manual 文档转换为结构化、清晰、面向 LLM 友好的 Markdown Corpus。

> **重要声明 / Copyright & Usage Notice**
> 1. 本项目**不提供、不分发、不内置**任何版本的 LS-DYNA 官方手册（Manual PDF 或其他形式的版权文本）。
> 2. 用户需要自行准备合法获得的 LS-DYNA Manual 文件。
> 3. 用户需要自行配置并提供文档解析所需的后端服务或 API 凭证。

---

## 项目当前状态

本项目当前处于**早期设计与架构初始化阶段（v0.1.0-dev）**。

当前仓库仅包含项目基础结构、规范定义和接口设计文档，**尚未提供完整的自动化转换运行功能**。

---

## 预期工作流与输入输出

未来版本的目标工作流如下：

### 1. 预期输入
- **Manual PDF**：用户本地合法获取的 LS-DYNA Keyword Manual（如 Volume I, Volume II 等）；
- **配置文件**：用户指定的解析后端（如通用 OCR/Document Parsing API、兼容 OpenAI 接口的模型后端等）及转换参数。

### 2. 预期输出
转换完成后，项目将在用户指定的本地工作区生成标准化的 Manual Corpus：
- `corpus.yaml`：语料库级元数据（包含 Manual 版本、构建时间、统计信息等）；
- `manifest.jsonl`：逐条 Keyword 的索引与来源映射（包含关键字名称、章节、源 PDF 页码范围、输出路径等）；
- `markdown/`：按 Keyword 切分的标准化 Markdown 文档；
- `reports/`：转换过程报告与质量检查摘要。

---

## 目录结构概览

```text
lsdyna-manual-builder/
├── README.md               # 项目说明
├── LICENSE                 # 开源许可证 (Apache-2.0)
├── pyproject.toml          # Python 项目与构建配置
├── .gitignore              # Git 忽略配置（严格排除 Manual、PDF、密钥与工作区临时数据）
├── docs/                   # 设计与规范文档
│   ├── corpus-format.md    # Corpus 输出规范说明
│   ├── markdown-style.md   # Keyword Markdown 格式风格说明
│   └── parser-interface.md # 解析后端抽象接口说明
├── src/lsdyna_manual/      # 核心源代码包骨架
│   ├── parser/             # PDF/文档初步解析模块
│   ├── providers/          # 外部解析 API / 模型后端适配层
│   ├── reconstruction/     # LS-DYNA 语义重建（Card/参数/跨页结构恢复）
│   ├── markdown/           # Markdown 生成与格式化
│   ├── manifest/           # 索引与元数据生成 (manifest.jsonl, corpus.yaml)
│   ├── validation/         # 转换质量校验（针对 Corpus，非 .k 文件）
│   └── cli/                # 命令行入口骨架
├── configs/                # 配置文件模板
│   └── example.yaml        # 示例配置
├── tests/                  # 测试套件
│   └── synthetic/          # 人工合成测试用例（不包含官方手册版权内容）
└── workspace/              # 本地运行与输出工作区（默认不纳入版本控制）
```

---

## 许可证

本项目采用 [Apache-2.0](LICENSE) 许可证开源。

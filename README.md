# LS-DYNA Manual to Markdown

将用户自行提供的 LS-DYNA Keyword Manual 和 Theory Manual 转换为按条目组织、
保留来源页码并附带质量报告的 Markdown 文档集合。

准备好 Manual、远程 API 或本地模型以及配置文件后，一条命令即可生成最终结果：

```powershell
.\.venv\Scripts\manual-to-markdown.exe build configs\local.yaml
```

程序会自动完成文档检查、页面解析、断点续跑和 Markdown 重建。

## 支持范围

- R12-R17 Keyword Manual，已完成文档检查验证；
- R14-R17 Theory Manual，已完成文档检查验证；
- Windows、Linux 和 WSL；

R17 已完成三册 Keyword Manual 和 Theory Manual 的完整构建验证；R12-R16 的后续解析
可以运行，但尚未完成同等范围的验证。当前为公开测试阶段，模型识别或复杂排版仍可能
产生需要复核的 warning。

## 配置需求

以下为构建完整版本 Manual 时的建议配置，实际占用会随 Manual 页数和模型文件而变化：

- 64 位 Windows、Linux 或 WSL，Python 3.10 或更高版本；
- 4 核或以上 CPU、16 GB 或以上内存，并为模型、运行时、中间文件和最终结果预留至少 30 GB 可用磁盘空间；
- 使用远程 API 时不需要独立显卡，但需要稳定的网络连接；
- 使用本地模型时，建议配备兼容 `llama.cpp` 和 PaddlePaddle 的独立显卡，显存容量不低于 8 GB。

## 安装

Windows PowerShell：

先安装 [Poppler for Windows](https://github.com/oschwartz10612/poppler-windows/releases)，
并将 `pdftotext.exe` 所在目录加入 `PATH`，然后执行：

```powershell
git clone https://github.com/945144024/lsdyna-manual-to-markdown.git
Set-Location lsdyna-manual-to-markdown
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

如果系统使用 Python Launcher，将 `python -m venv .venv` 换成
`py -3.12 -m venv .venv`。

Debian / Ubuntu：

```bash
sudo apt-get install python3 python3-venv python3-pip poppler-utils
git clone https://github.com/945144024/lsdyna-manual-to-markdown.git
cd lsdyna-manual-to-markdown
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

## 准备 Manual

从 LS-DYNA 官方渠道取得 Manual，并放入 `manuals/`。仓库不会下载或分发 Manual。

```text
manuals/
├── LS-DYNA_Manual_Vol_I_R17.pdf
├── LS-DYNA_Manual_Vol_II_R17.pdf
├── LS-DYNA_Manual_Vol_III_R17.pdf
└── LS-DYNA_Manual_Theory_R17.pdf
```

复制配置模板：

```powershell
Copy-Item configs\example.yaml configs\local.yaml
```

Linux 使用：

```bash
cp configs/example.yaml configs/local.yaml
```

编辑 `configs/local.yaml`，设置 Manual 版本、输入目录和输出目录：

```yaml
manual:
  release: "R17"
  manuals_dir: "./manuals"

output:
  corpus_dir: "./workspace/run_r17"
```

然后选择一种解析方式。

### 远程 API

使用[百度 AI Studio PaddleOCR](https://aistudio.baidu.com/paddleocr) 提供的 API：

```yaml
parser:
  provider: "paddleocr-vl-remote"
  model: "PaddleOCR-VL-1.6"
  job_url: "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
  api_key: "<your API key>"
  max_batch_pages: 1
```

### 本地模型

本地方式由以下组件组成：

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)：文档版面解析和工作进程；
- [PaddleOCR-VL-1.6 GGUF](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF)：
  主模型文件和 mmproj 文件；
- [llama.cpp / llama-server](https://github.com/ggml-org/llama.cpp/releases)：
  加载 GGUF 并提供本地推理服务；
- [PaddlePaddle](https://www.paddlepaddle.org.cn/install/quick)：PaddleOCR 的运行基础。

将这些组件准备好后填写路径。下面的 `C:/path/to/...` 都是占位符，必须替换为
你实际保存文件的位置；这些文件不需要放在固定目录。`runtime_dir` 可以保留默认值，
相对路径以执行命令时的工作目录（通常是仓库根目录）为基准。

```yaml
parser:
  provider: "paddleocr-vl-local"
  model: "PaddleOCR-VL-1.6"
  max_batch_pages: 1
  local:
    runtime_dir: "./.runtime/paddleocr-local"
    llama_server_path: "C:/path/to/llama-server.exe"
    model_path: "C:/path/to/PaddleOCR-VL-1.6-GGUF.gguf"
    mmproj_path: "C:/path/to/PaddleOCR-VL-1.6-GGUF-mmproj.gguf"
    paddleocr_python: "C:/path/to/PaddleOCR/venv/Scripts/python.exe"
    auto_prepare_runtime: false
```

`paddleocr_python` 是安装了 PaddleOCR 的 Python 可执行文件，用于运行本地解析 worker。
如果将 `auto_prepare_runtime` 设为 `true` 并在 `build` 命令中使用
`--allow-runtime-install`，可以省略这个字段，程序会在 `runtime_dir` 下组装独立的
PaddleOCR 运行环境。自动准备不会安装显卡驱动；`llama-server`、GGUF 和 mmproj 文件
应先下载并配置好路径。

完整字段和注释见 `configs/example.yaml`。

## 生成 Markdown

Windows：

```powershell
.\.venv\Scripts\manual-to-markdown.exe build configs\local.yaml
```

需要自动准备 PaddleOCR 环境时：

```powershell
.\.venv\Scripts\manual-to-markdown.exe build configs\local.yaml --allow-runtime-install
```

Linux / WSL：

```bash
manual-to-markdown build configs/local.yaml
```

运行中断、远程配额暂停或个别页面失败后，重新执行同一条命令即可继续。已经验证的
页面结果不会重复请求模型。

生成内容位于配置的 `output.corpus_dir`：

```text
corpus_root/
├── corpus.yaml                 # 本次构建的版本、来源和汇总信息
├── manifest.jsonl              # 每个 Markdown 条目的路径、来源页和质量状态
├── markdown/                   # 最终 Markdown 文档
│   ├── volume-1/               # Keyword Manual Volume I
│   ├── volume-2/               # Keyword Manual Volume II
│   ├── volume-3/               # Keyword Manual Volume III
│   └── theory/                 # Theory Manual
└── reports/
    ├── summary.json             # 构建结果与成功/warning/failed 汇总
    ├── issues.jsonl             # 需要复核的问题及对应页面
    └── text_layer_comparison.json # PDF 文本层抽样校验
```

每个条目的状态可在 `manifest.jsonl` 的 `status` 字段中查看，三个状态的数量汇总位于
`reports/summary.json`；具体问题及对应页面记录在 `reports/issues.jsonl`：

- `success`：条目已正常生成，可以直接使用；
- `warning`：条目仍包含 Markdown 正文，但存在建议复核的问题；
- `failed`：条目未能生成完整正文，不应作为完整内容使用。

## 已知限制

- OCR 和视觉模型可能产生识别误差、空结果或结构歧义；
- 复杂表格、公式、图片和相邻条目边界可能需要人工复核。

## 许可证与版权

项目代码采用 [MIT](https://github.com/945144024/lsdyna-manual-to-markdown/blob/main/LICENSE)
许可证。LS-DYNA Manual、PaddleOCR、模型和 `llama-server` 适用各自的版权与许可
条款。本项目不附带 Manual、模型或第三方可执行文件，也不代表其权利方提供背书。

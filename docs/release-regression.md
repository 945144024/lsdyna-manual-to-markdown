# 跨版本语义回归

本文定义 R12-R16 语义样本和独立 holdout 的建立方法。样本只保存源文件 hash、
SectionMap 身份和页码，不保存 Manual 正文。

## 前置条件

每个版本必须先准备对应 PDF，并执行 `inspect` 生成 PageMap / SectionMap。不得借用其他
版本的导航产物，也不得在缺少源 PDF 时手工构造 manifest。R12-R13 仅包含三册
Keyword Manual；R14-R16 还包含 Theory Manual。

## 冻结规则

- 固定样本 seed 为 `20260821`；
- 独立 holdout seed 为 `20260822`；
- 两者使用相同的默认分层目标 `short/medium/long = 3/4/3`；
- holdout 使用 `--holdout-of` 排除固定样本中的全部章节；
- manifest 生成后即为选择合同，复查必须使用 `--sample-manifest`；
- Provider 解析只在 manifest 冻结后进行，模型输出不得反向影响样本选择。

目录合同：

```text
workspace/regression/<release>/
├── intermediate/
├── semantic-sample/
│   ├── sample_manifest.json
│   └── sample_detection.json
└── semantic-holdout/
    ├── sample_manifest.json
    └── sample_detection.json
```

固定样本：

```powershell
manual-to-markdown sample-regression `
  --manuals-dir manuals `
  --release R16 `
  --intermediate-dir workspace/regression/r16/intermediate `
  --pageir-dir workspace/regression/r16/pageir `
  --output-dir workspace/regression/r16/semantic-sample `
  --seed 20260821
```

独立 holdout：

```powershell
manual-to-markdown sample-regression `
  --manuals-dir manuals `
  --release R16 `
  --intermediate-dir workspace/regression/r16/intermediate `
  --pageir-dir workspace/regression/r16/pageir `
  --output-dir workspace/regression/r16/semantic-holdout `
  --seed 20260822 `
  --holdout-of workspace/regression/r16/semantic-sample/sample_manifest.json
```

在尚无 PageIR 时，`sample_detection.json` 中的 `not_parsed` 是预期状态。完成样本解析后，
以 `--sample-manifest` 重新检测冻结集合，不重新抽样。

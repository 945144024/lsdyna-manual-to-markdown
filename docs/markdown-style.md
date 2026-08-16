# Markdown Style Specification (v0.1 Draft)

本文档说明生成的 Keyword Markdown 文档的组织规范与风格要求。

## 1. 基本组织原则

- **单 Keyword 单文件**：每个 LS-DYNA Keyword 独立生成一个 `.md` 文件（例如 `*MAT_024.md` 或 `*ELEMENT_SHELL.md`）。
- **结构保持**：严格保留原始手册的语义层级，包括：
  - Keyword 主标题与别名/选项；
  - 简要描述（Purpose / Description）；
  - Card 布局（以统一 Markdown 表格呈现）；
  - Parameter 变量说明（字段含义、类型、默认值）；
  - Remarks（详细备注与物理背景说明）；
  - 物理与数学公式（以 LaTeX 语法呈现）。
- **图片暂不处理**：当前版本暂不提取、重绘或处理 Manual 中的位图/矢量插图，可保留占位说明或文字描述。

## 2. 推荐 Markdown 结构模板

```markdown
# *KEYWORD_NAME

## Purpose
简要说明该关键字的作用与适用场景。

## Card Summary
（可选，说明该关键字包含多少组卡片及条件卡片关系）

## Card Definition

### Card 1
| Field | Name 1 | Name 2 | Name 3 | Name 4 | Name 5 | Name 6 | Name 7 | Name 8 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Type** | I | F | F | F | F | F | F | F |
| **Default** | none | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

### Variable Definitions
- **Name 1** (`Type: I`, `Default: none`): 参数 1 的详细含义与取值约束。
- **Name 2** (`Type: F`, `Default: 0.0`): 参数 2 的详细含义与取值约束。

## Remarks
1. **Remark 1**: 关于物理模型与计算稳定性的说明。
2. **Remark 2**: 涉及公式说明：
   $$ \sigma_{y} = \sigma_0 + E_p \varepsilon^p_{eff} $$

## References
相关参考文献或标准引用说明。
```

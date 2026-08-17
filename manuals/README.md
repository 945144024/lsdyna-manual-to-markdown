# Manual 文件目录

本目录用于存放用户自行取得并获准使用的 LS-DYNA 官方 Manual PDF。仓库不提供、下载或分发 Manual。

支持识别的官方文件名示例：

- `LS-DYNA_Manual_Volume_I_R13.pdf`
- `LS-DYNA_Manual_Vol_II_R17.pdf`
- `LS-DYNA_Manual_Theory_R17.pdf`

同一目录可以保存多个 release。运行时使用 `manual.release` 选择一个版本，并可处理该版本下任意非空 Keyword / Theory 组合。非标准文件名应通过 `manual.documents` 显式声明类型和卷号。

`.gitignore` 排除本目录中的全部内容，仅保留本 README。不得提交 Manual、压缩包、解压文本、渲染页或其他版权材料。

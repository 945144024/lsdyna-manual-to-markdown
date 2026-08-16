# Workspace Directory

本目录用于存放用户本地运行生成的中间缓存、解析输出与日志数据，例如：

- `run_*/`：`lsdyna-manual build` 与 `inspect` 的本地输出；
- `robustness/`：多 release 的本地检查输出与汇总数据；
- `manual_section_checklist.xlsx`：由本地脚本生成的人工核对表；
- `generate_manual_checklist.py`：重新生成上述核对表的本地脚本。

本目录内容已被 `.gitignore` 排除，不会被提交至 Git 仓库。请勿在此目录中存放未授权分发的敏感数据或版权文件。

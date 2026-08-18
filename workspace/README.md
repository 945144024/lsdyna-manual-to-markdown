# 本地工作目录

本目录存放运行时生成且不进入版本控制的中间产物和报告：

- `run_*/intermediate/`：PageMap、SectionMap、TOC 索引和 inspection issues；
- `regression/`：R12-R17 回归矩阵、渲染页和审阅材料；
- `run_*/parsing/raw/`：Provider raw artifact 和 transport PDF；
- `run_*/parsing/pageir/`：Canonical PageIR；
- `run_*/parsing/state.json`：页面级 checkpoint；
- `run_*/markdown/`、`run_*/manifest.jsonl` 与 `run_*/reports/`：`reconstruct` 或 `build` 生成的本地 Corpus 与质量报告；
- `regression/<release>/semantic-sample/`：固定 seed 的分层语义抽样 manifest 与检测结果。

`.gitignore` 排除本目录中的全部内容，仅保留本 README。目录可能包含 Manual 派生内容、signed URL 的脱敏副本和本地运行信息，不得将其提交或分发。

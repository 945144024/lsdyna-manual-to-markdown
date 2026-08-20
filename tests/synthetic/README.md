# 合成测试用例

本目录说明人工合成的 LS-DYNA 风格测试约定。测试页面主要由测试代码动态生成；新增 fixture 也应放在本目录。

要求：

- 不得复制、截取或分发 LS-DYNA 官方 Manual 的原文、图片或 PDF 片段；
- Keyword、卡片、公式和跨页排版样本必须由开发者编写或程序生成；
- 合成用例可以模拟不同 release 的版式差异，例如重复印刷页码、无页脚区域和 TOC 页码错误；
- 一键 `build`、checkpoint 恢复和 Keyword/Theory 统一 manifest 的端到端测试必须使用合成输入，不得依赖开发机上的官方 Manual；
- PageIR v0.2 的 rowspan/colspan、Windows/POSIX 路径和 Poppler UTF-8 行为必须使用
  合成对象或 mock 输出覆盖，不得把真实页面固化为 fixture；
- fixture 不得包含真实 API Key、Authorization header 或可用的 signed URL。

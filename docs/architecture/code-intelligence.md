# Code Intelligence v3

Repo Index v3 在现有 AST/符号/import/依赖导航之上增加有限的 Python
CallRecord 和影响分析。缓存文件路径继续使用 `.pico/index/repo-index-v2.json`
以兼容旧工作区，但缓存 payload 的 `schema_version` 已升级为
`repo-index-v3`；旧 schema 会被丢弃并重建。

## CallRecord

每个 Python 文件保存：

- 调用方 lexical symbol；
- 被调用文本，如 `normalize` 或 `worker.run`；
- 文件、行和列；
- 解析结果、resolution 类型和 confidence。

静态解析只在证据充分时标记 `exact` 或 `unique_name`。动态分派、多个同名
候选和无法判断的调用保留为 `ambiguous`/`unresolved`，不会伪装成精确
Call Graph。

## analyze_impact

`analyze_impact` 可以以文件或符号为目标，输出：

- definitions；
- direct callers / direct callees；
- 在 `depth=2` 时额外给出 bounded indirect callers / indirect callees；
- reverse importers；
- related tests；
- candidate files；
- confidence、unresolved count 和 diagnostics。

结果只接受 `depth=1..2`，有界输出，最多保留有限数量的候选项，并保留截断信息。它是仓库导航
证据，不替代源码阅读、编译器和测试。ExecutionPolicy 将它视为 discovery
tool：跨文件修改前优先缩小影响范围，但仍必须在首次编辑前完成源码确认。

当前 Java、JavaScript/TypeScript、Go、Rust 继续使用保守结构提取；本版本
不引入 Tree-sitter，也不声称这些语言拥有 compiler-accurate Call Graph。

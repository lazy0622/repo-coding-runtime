# Repo Coding Runtime：架构取舍

本文记录当前实现为什么这样设计，以及它刻意没有解决什么。它是面试和后续演进的事实依据，不是功能宣传页。

## 1. 自研 Runtime，而不是只编排框架节点

项目把 Agent Loop、工具协议、审批、Checkpoint、Memory、Repo Index、TaskGraph 和运行工件放在同一运行时中，目的是能解释每一步状态与失败边界。代价是需要自行维护调度、兼容和可观测性；如果目标只是快速搭业务流，LangGraph 等成熟框架更省成本。本项目选择前者，是因为核心目标是 coding-agent harness 工程能力，而不是工作流搭建速度。

## 2. Repo Index 是导航缓存，不是代码真相

Python AST 提供精确结构事实；Java、JavaScript/TypeScript、Go 和 Rust 当前使用保守正则提取符号与 import，只适合缩小搜索范围。Agent 在修改前仍必须读取源码，验证仍由编译器/测试完成。索引持久化到 `.pico/index/repo-index-v2.json`，mtime/size 改变后重新计算内容指纹；损坏或版本不匹配的缓存会被丢弃。选择无外部依赖保证本地可运行，代价是多语言精度低于 tree-sitter/LSP。

## 3. 有界并发，不做无限并行

TaskGraph 只并发执行依赖已满足的 ready batch，默认 2、上限 4。这样可以降低墙钟时间，同时限制模型调用、文件句柄和服务端限流压力。Supervisor 在主线程合并结果并写 checkpoint，避免多个子线程同时修改图状态。当前仍是单进程线程池，不是分布式调度器；provider 客户端还应具备真正的请求取消能力。

## 4. 写子 Agent 必须隔离，且不自动合并

研究任务默认只读。写任务需要任务声明 `mode=write` 和调用方设置 `allow_write_subagents=true` 两次授权，并强制使用 detached Git worktree。允许的写能力只有严格补丁与验证 shell；结果保存为 `workspace.patch`，主工作区不变，人工或上层 Supervisor 决定是否合并。代价是产生需要清理的 dirty worktree，也暂不支持多 patch 自动冲突解决。

## 5. 两层 Benchmark，不混淆结论

内置 deterministic benchmark 测试工具协议、上下文压缩、恢复和安全回归，结果稳定但不代表模型能解决真实 issue。SWE-bench adapter 在固定真实仓库 commit 上生成 patch，并把 `predictions.jsonl` 交给官方 Docker harness；只有官方 `resolved` 结果才可称为 solve rate。推荐固定 10 个 smoke、20 个开发、50 个最终样本，并锁定模型、token/tool budget、超时和实例 ID，防止挑结果。

## 6. 安全指标同时衡量保护与可用性

单看攻击拦截数会鼓励“全部拒绝”。V3 报告 attack block rate、benign false-block rate 和 secret leak rate。当前测试覆盖路径逃逸、只读写入、审批拒绝、正常仓库读取/搜索以及环境 secret 的文本和结构化工件脱敏；它仍不等价于完整渗透测试，也未覆盖网络数据外传和 prompt injection 全链路。

## 7. 显式任务合同优先于意图猜测

`task_mode=auto` 保留自然语言意图识别，但 CLI 和子 Agent 可以显式选择 `inspect`、`edit` 或 `verify`。这避免“解释如何修改”被误判为必须写文件，也避免含糊的修改任务过早返回。代价是调用方需要在编排层正确声明任务类型；报告会记录最终模式，方便审计误分类。

## 8. Blocked 是有证据的终态

缺少密钥、外部依赖或必要用户输入时，继续重试会浪费预算，直接 final 又会制造假成功。因此 Runtime 接受结构化 `<blocked>`，要求至少提供 reason，并保存 evidence、required_input 和 category。Blocked 与 failed 分离：前者表示当前信息边界下不能安全完成，后者表示执行发生错误。

## 9. 分阶段预算约束 Agent，而不是替代模型推理

Explore、diagnose、首次 edit、verify 和 repair 分别计数。预算耗尽时 Supervisor 先注入一次明确指令，再阻止无价值的重复读取或验证；每次阶段迁移和尝试数都进入 Report。固定预算提高可预测性，但过小会伤害复杂仓库任务，所以 Benchmark 必须同时报告通过率、首次编辑步数、只读工具比例和误阻断，而不能只追求更早写代码。

## 10. 隐藏验证在 Agent 退出后注入

公开 verifier 用于快速反馈；隐藏测试文件直到 Agent 完成后才复制进临时工作区，用于发现针对公开样例的过拟合。隐藏验证只增强本地固定任务的可信度，不等于外部真实仓库泛化能力。真实 issue 仍通过固定 SWE-bench 子集生成 patch，并由官方 Docker harness 独立裁决。

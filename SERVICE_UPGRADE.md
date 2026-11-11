# Repo Runtime：可恢复的仓库任务执行服务

实施日期：2026-11-08。代码目录：`pico/service/`。保留原有终端 Runtime，新服务作为可选依赖安装，不改变原 CLI 的依赖要求。

## 当前可运行的闭环

工作台或 API 提交任务 → PostgreSQL 持久化 → 两个 worker 竞争领取 → 每次尝试创建独立 Git worktree → 运行原 Pico → 封存补丁及摘要 → 在干净 worktree 中重放补丁并运行服务端指定的行为检查 → 发布明确的成功/失败结果。

本机工作台：`http://127.0.0.1:8010/`；接口说明：`http://127.0.0.1:8010/docs`。访问令牌保存在忽略上传的 `.env.service` 中，变量名 `RUNTIME_API_TOKEN`。在工作台填入该值并连接；不需要把密钥发到聊天或写入 GitHub。

工作台中的 `demo-fixture` / `demo-recovery` 是标明的确定性执行器，用于演示服务与故障处理；`demo-agent` 才调用真实 DeepSeek 模型。不能把前两者的结果当作模型修复成功率。

## 新增机制与边界

| 机制 | 实现与验收范围 |
|---|---|
| 幂等提交 | 同键同输入返回原任务；同键不同输入返回 409；数据库唯一约束处理并发提交 |
| 原子领取 | `FOR UPDATE SKIP LOCKED`；数据库时间判断租约；不在事务里运行模型或命令 |
| 尝试代次 | 每次接管递增 generation；续租、封存和完成均在行锁下校验代次、owner 和租约 |
| 封存恢复 | 补丁写入并计算 SHA-256 后，checkpoint 与验证阶段原子落库；接管校验摘要与 base commit |
| 结果发布 | 只有当前有效所有者才能发布；成功还要求验证阶段和检查通过；旧代次不能覆盖当前结果 |
| 验证一致性 | 首次执行和接管都只验证固定 base commit 加封存补丁，排除 Agent 工作区中的运行目录和私密文件 |
| 取消 | 排队任务直接取消；执行任务进入 cancelling，worker 停止当前子进程树后确认取消 |
| 持久化事件 | 状态变更与递增事件序号在同一事务中写入；支持 Last-Event-ID 和 after 续读 |
| 工作区 | 每个 job / generation 独立 worktree；保留产物供审阅，不自动合并，也不删除脏工作区 |
| 进程环境 | Agent 不继承数据库和 API 控制令牌；验证进程也不继承模型密钥 |

**首版恢复到“已封存补丁后的验证阶段”。** 封存前中断会创建新尝试，从固定 base commit 重新运行模型；本轮没有实现任意模型步骤的无损迁移恢复。旧 Runtime 自己的 session/checkpoint 机制仍在，但不冒充新服务的跨 worker 恢复保证。

正常取消会确认当前受管进程退出；若 worker 已失联且取消租约到期，则记录 `process_exit_confirmed=false`。不能把逻辑取消解释成已证实所有外部副作用停止。

当前是有访问令牌的单机开发服务，绑定回环地址。演示配置使用 Host 执行，worktree 不构成安全沙箱；原 Runtime 的可选 Docker 接口保留，但本轮没有完成服务层 Docker 隔离验收。未实现多租户或生产部署，也不承诺所有 shell / 网络副作用 exactly-once。

## 部署与复现

需要 Python 3.11、Git、PostgreSQL。新服务的 PostgreSQL 用户应拥有独立数据库，配置文件只放本地。

```powershell
py -3.11 -m venv ../.venvs/runtime-service
& ../.venvs/runtime-service/Scripts/python.exe -m pip install -e ".[service]"
& ../.venvs/runtime-service/Scripts/python.exe scripts/setup-service-demo.py
Copy-Item configs/runtime-service.env.example .env.service
# 本地填写数据库连接、随机访问令牌和模型密钥。
./scripts/start-runtime-service.ps1
```

`setup-service-demo.py` 只创建独立的故障演示仓库；已有配置不会被覆盖。`repositories.json` 是受信任的服务器配置：映射仓库 ID、源路径、验证 argv 和可选演示执行器。请求只能选仓库 ID 和提供任务文字，不能指定任意路径、shell 命令或验证器。

这次本机使用专门初始化的 PostgreSQL 17.6 实例，监听 `127.0.0.1:5438`。数据库账号不是集群超级用户。Docker 引擎未就绪，因此未将容器启动当成部署前提。已有本机集群可用 `scripts/start-local-postgres.ps1` 启动；该脚本不会初始化或清空数据。

API 与两个 worker 的日志在 `artifacts/service/logs/`。运行目录、补丁、数据库凭据、令牌及模型密钥均不上传。

真实模型配置为 `deepseek-flash`。目前原 Pico 的 Anthropic 兼容适配器会关闭思考模式以保持旧多轮工具协议兼容，因此本项目二的这次实测**不是 Flash High**；DataTrail 的 Flash High 配置不变。High 的推理块续传应作为独立兼容性改造验收，不能仅改一个名字就宣称已支持。

## 接口

所有 `/api/` 接口需要 `Authorization: Bearer <本地令牌>`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 检查 API 与真实数据库连接 |
| GET | `/api/repositories` | 已注册仓库 ID 与执行器类型 |
| POST | `/api/jobs` | JSON 为 repository_id / task；必须带 Idempotency-Key |
| GET | `/api/jobs` | 最近任务列表 |
| GET | `/api/jobs/{id}` | 状态、结果及尝试历史 |
| POST | `/api/jobs/{id}/cancel` | 请求取消，不覆盖已完成终态 |
| GET | `/api/jobs/{id}/events` | SSE；支持 after、Last-Event-ID、follow=false |
| GET | `/api/jobs/{id}/patch` | 下载已接受的补丁 checkpoint；再次校验摘要 |

## 本轮验证结果

- **38 项相关回归通过**：23 项新增服务/Windows 兼容测试，加 15 项原 Runtime 的完成证据、评测器、验证回归。服务测试连接真实 PostgreSQL，并在独立临时 schema 中执行。
- 真实进程故障演示：终止第一个 worker，新 worker 接管到 generation 2，从封存补丁继续验证，未再次运行演示 Agent；旧代次的完成提交被拒绝。尝试历史为 `expired → succeeded`。
- 真实模型单样例复测：DeepSeek 修复独立演示仓库的加法函数，在干净 worktree 重放补丁后，服务端仓库外的 5 项行为检查通过，最终一次用时约 36.3 秒；原源仓库仍保留原始故障，补丁作为产物保存。这是同一简单样例的重复执行，不是多个独立任务的成功率。
- 浏览器检查：令牌连接、任务提交、状态与事件查看、确定性任务成功均通过，未观察到页面脚本错误。

复现命令：

```powershell
$env:PYTHONUTF8 = '1'
& ../.venvs/runtime-service/Scripts/python.exe -m pip install pytest httpx
& ../.venvs/runtime-service/Scripts/python.exe -m pytest tests/service tests/test_completion_evidence.py tests/test_evaluator.py tests/test_verification.py -q
& ../.venvs/runtime-service/Scripts/python.exe scripts/demo_service_recovery.py
```

`demo_service_recovery.py` 使用独立 PostgreSQL schema 和明确标记的确定性执行器，实际终止进程，保留恢复结果在本地。它不是模型评测，也不是容器故障演示。

## 发现并保留的失败

首次真实模型任务已经修好代码，但补丁收集使用负向 `.pico` pathspec 时，Git 因实际存在的忽略目录而返回失败。本轮改成显式字面量文件清单，并补充忽略运行目录、排除 `.env` 与包含新文件的回归；旧失败任务未改写成成功。

原评测器在 Windows 上把 WindowsApps 的 python3 启动别名当成可用解释器，验证返回 9009。本轮对窄范围 Python `-c` 兼容路径增加当前解释器回退，保留真实 Python 安装与 shell 命令链语义；修复后上述旧回归通过。

这些结果证明当前限定合同及一个简单修复样例可运行，不是生产可靠性、多个真实仓库成功率或官方 SWE-bench 成绩。下一阶段需要固定多个独立真实任务，扩大崩溃位置与取消并发矩阵，补充真实 Docker 隔离及模型 High 协议兼容性验证。

## 代码来源与评审

独立工作目录基于原项目 `78d5297`。`7bfb430` 单独保存实施前已经存在的本地 Runtime 修复；服务升级在其后提交，方便区分旧积累和新增贡献。原目录的 tracked diff 哈希在升级前后相同，未覆盖其未提交修改。

## 独立审查修复（分支 `opus/runtime-review`，基于 `81985c4`）

以下三项均先在 `81985c4` 上用 `tests/service/test_review_regressions.py` 复现失败，再修改 `pico/service/worker.py`：

1. **取消与失败竞争导致 worker 进程退出**：取消已落库、Agent 在下一次心跳前非零退出时，失败分支的 `finish(failed)` 被正确拒绝（`CancellationRequested`），但 worker 只捕获 `LeaseLost`，异常逃出 `run_one` 终止循环。现统一经 `_publish_quietly` 改记为 `cancelled`，并保留被取代的失败原因。
2. **一次心跳异常即放弃尝试**：任何续租异常都会立即杀掉健康的 Agent、浪费一次尝试并重跑模型。现仅 `LeaseLost` 立即放弃；暂时性错误在本地保守租约期限（从请求发出前计时）内重试，期限将尽时停止子进程。外层 `serve()` 循环在 `claim` 等数据库异常时退避重试而不是退出进程。发布仍由数据库代次/租约校验约束。
3. **子进程生命周期未绑定 worker（Windows）**：worker 被强杀后 Agent 继续运行；Agent 根进程退出后其后台孙进程也不会被回收。现每次执行创建 kill-on-close Job Object，`kill_process_tree` 终止整个 Job。POSIX 改为总是 `killpg`，但 worker 被强杀时的孤儿问题在 POSIX 上未处理、未测试。

以 strict xfail 记录、未修复：空补丁在验证器本就通过时被发布为 `succeeded`；每次尝试在受信任源仓库注册的 worktree 不会移除。

## 审查修复复核与接入（2026-11-08）

在 `1a2f289` 的基础上补齐 Job Object 绑定失败的回收路径：只有成功绑定后才通过 Job 终止进程；绑定失败时终止实际创建的进程树，等待退出并保留绑定错误。新增实际子进程故障注入回归，以及常驻 worker 连续两次数据库故障后成功领取的退避测试。

租约本地时钟用于保守自停，不提供硬实时期限保证；数据库所有权校验仍控制结果发布。Job Object 的启动后绑定仍有竞争窗口，也不提供文件或网络安全隔离。两个 strict xfail 对应未采用行为变更的空补丁和工作区保留策略，不能计入通过数量。

接入前相关回归：**46 passed、2 xfailed、0 skipped**（真实 PostgreSQL，121.17 秒）；实际终止 worker 的封存恢复演示再次通过。新进程清理用例验证绑定抛错后子进程已退出，且原绑定错误没有被等待超时遮蔽。

## 五个真实仓库任务（2026-11-08）

已经通过部署的 `/api/jobs` 执行 humanize 三个、itsdangerous 两个独立历史修复任务。五项补丁均通过仓库外行为检查和未修改的原有回归；其中只有一项 Agent 自然完成，另外四项达到工具步数预算后由服务验证补丁通过。两种指标分别报告，不把 5/5 补丁接受率写成通用模型完成率。公开问题、冻结方法、原始提交、补丁及 token/时间摘要见 [真实任务验证](benchmarks/service_real/README.md)。

## 2026-11-11 执行器对照

同一五题的新 Pico 回归为 4/5 独立验收、5/5 自然结束；隔离 Pi 1.5.1 接入为
2/5 独立验收、3/5 自然结束。保留所有失败与原基线，未据此切换默认执行器。
预算和工具语义不同，不能把这次集成对照解释成通用内核排名。
完整条件、执行合同冒烟、配置失败与边界见 [Pi 实验](benchmarks/pi_compare/README.md)。

实验复用原 PostgreSQL 状态源与验收，不启动新队列。原仓库 ID 仍使用 Pico，
服务端显式注册的 `--pi` 别名才使用 Pi。受信任的 `agent_command` 现在支持整项
`{task}` 参数，作为单一 argv 原样传给执行器；适配器在 `--` 后接收它，用户输入
不能借任务文本改变执行器选项。任务文本中的其他占位符不会递归替换。

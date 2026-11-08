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

# Execution Sandbox

Repo Coding Runtime 将工具策略和进程隔离分成两层：

```text
ToolGateway
  ├─ schema / approval / command policy / path boundary
  └─ ExecutionBackend
       ├─ HostExecutionBackend
       └─ DockerExecutionBackend
```

## Host 模式

Host 是默认兼容模式，沿用现有 Windows/POSIX 命令执行逻辑。它适合本地
开发和不需要 OS 级隔离的任务，但 deny-list、环境变量过滤和 Git Worktree
都不能等价替代进程沙盒。

## Docker 模式

Docker 必须通过 `--sandbox docker` 显式选择。一个 Runtime 实例创建一个
容器 session，多个 Shell/verifier 调用通过 `docker exec` 复用该 session，
以保留工作区内安装的依赖和进程状态。任务结束或 Runtime 关闭时显式清理
容器。

默认约束：

- `network=none`；
- 非 root UID:GID；
- `cap-drop=ALL` 和 `no-new-privileges`；
- 只读容器根目录，`/tmp` 使用受限 tmpfs；
- 只传入 Runtime 已经 allowlist 过滤且不含明显 secret 的环境变量；
- CPU、内存和 PID 数量有上限；
- Docker preflight 或容器创建失败时直接报错，不回退到 Host。

Docker Sandbox 保护的是进程执行边界，不能保证代码逻辑正确，也不能阻止
Agent 在已挂载工作区内生成错误 Patch。因此写任务仍应结合 detached Git
Worktree、PatchJournal、Verifier 和 guarded rollback。

## 生命周期与证据

`ExecutionResult` 记录退出码、耗时、backend、sandbox mode、timeout、OOM
和资源限制原因。Run report 记录非敏感的 SandboxConfig；trace 的
`tool_finished` 事件同时记录执行后端和终止原因。容器 id 只作为运行期诊断
信息，环境变量值不会进入 report。

Docker 集成测试默认跳过，设置 `PICO_DOCKER_INTEGRATION=1` 后才运行，避免
普通 CI 依赖 Docker daemon、镜像下载和外部工具链。

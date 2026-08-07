# Repo Coding Runtime V2 Demo

从仓库根目录运行：

```bash
python scripts/run_v2_demos.py
```

Demo 覆盖：

- Supervisor 调度带依赖关系的两个只读子 Agent，并在父运行目录保存子任务 artifacts；
- 请求 Git worktree 隔离，在非 Git 工作区安全降级为只读共享模式并记录原因。

V2.1–V2.4 的结构化证据、重试/超时/恢复和代码闭环 Demo：

```bash
python scripts/run_v2_4_demos.py
```

它会验证一次成功的 `research → patch → verify`，以及一次验证失败后的
自动 `rollback`。完整状态保存在每个 workflow artifact 中。

默认输出写入 `artifacts/v2_demos/<timestamp>/`。

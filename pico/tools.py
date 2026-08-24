"""工具定义与执行辅助逻辑。

可以把这个文件看成 agent 的能力白名单：模型能申请哪些动作、这些动作
如何做参数校验，以及最终如何执行，都是在这里定义的。
"""

import json
import shutil
import subprocess
import textwrap
from functools import partial

from .command_runner import run_shell_command
from .patching import PatchJournal, apply_unified_diff, parse_unified_diff, preview_file_diff
from .repo_index import RepoIndex, render_index_result
from .sandbox import HostExecutionBackend
from .task_graph import TaskGraph, TaskGraphError
from .workspace import IGNORED_PATH_NAMES

BASE_TOOL_SPECS = {
    "list_files": {
        "schema": {"path": "str='.'"},
        "risky": False,
        "description": "List files in the workspace.",
    },
    "read_file": {
        "schema": {"path": "str", "start": "int=1", "end": "int=200"},
        "risky": False,
        "description": "Read a UTF-8 file by line range.",
    },
    "search": {
        "schema": {"pattern": "str", "path": "str='.'"},
        "risky": False,
        "description": "Search the workspace with rg or a simple fallback.",
    },
    "get_file_outline": {
        "schema": {"path": "str"},
        "risky": False,
        "description": "Build a persistent structural outline for Python, Java, JS/TS, Go, and Rust.",
    },
    "find_symbol": {
        "schema": {"name": "str", "path": "str='.'"},
        "risky": False,
        "description": "Find indexed symbol definitions by name or qualified name.",
    },
    "find_references": {
        "schema": {"name": "str", "path": "str='.'"},
        "risky": False,
        "description": "Find AST or conservative token references with line and scope evidence.",
    },
    "get_dependency_graph": {
        "schema": {"path": "str='.'"},
        "risky": False,
        "description": "Summarize indexed imports and best-effort internal dependency edges.",
    },
    "analyze_impact": {
        "schema": {"target": "str", "path": "str='.'", "depth": "int=1"},
        "risky": False,
        "description": "Find conservative callers, importers, related tests, and candidate files for a symbol or file.",
    },
    "get_changed_files": {
        "schema": {},
        "risky": False,
        "description": "Read the current Git changed-file list without executing a shell command.",
    },
    "run_shell": {
        "schema": {"command": "str", "timeout": "int=20"},
        "risky": True,
        "description": "Run a shell command in the repo root.",
    },
    "write_file": {
        "schema": {"path": "str", "content": "str"},
        "risky": True,
        "description": "Write a text file.",
    },
    "patch_file": {
        "schema": {"path": "str", "old_text": "str", "new_text": "str"},
        "risky": True,
        "description": "Replace one exact text block in a file.",
    },
    "preview_diff": {
        "schema": {"path": "str", "new_content": "str"},
        "risky": False,
        "description": "Preview a unified diff before changing a file.",
    },
    "apply_patch": {
        "schema": {"patch": "str"},
        "risky": True,
        "description": "Apply a strict unified diff atomically and create a rollback backup.",
    },
    "rollback_patch": {
        "schema": {"backup_id": "str"},
        "risky": True,
        "description": "Rollback a patch only if its affected files have not changed since application.",
    },
}

DELEGATE_TOOL_SPEC = {
    "schema": {"task": "str", "max_steps": "int=3"},
    "risky": False,
    "description": "Ask a bounded read-only child agent to investigate.",
}

V2_TOOL_SPEC = {
    "schema": {
        "tasks": "list=[] when resuming",
        "max_steps": "int=4",
        "max_task_attempts": "int=1",
        "task_timeout_seconds": "int=120",
        "isolate_worktrees": "bool=False",
        "max_concurrency": "int=2",
        "allow_write_subagents": "bool=False",
        "resume": "bool=False",
        "graph_id": "str=''",
    },
    "risky": False,
    "description": "Run or resume a bounded concurrent task graph; write tasks require explicit isolated-worktree authorization.",
}

V2_WORKFLOW_TOOL_SPEC = {
    "schema": {
        "goal": "str",
        "research_tasks": "list",
        "patch": "str",
        "verify_command": "str",
        "verify_timeout": "int=60",
        "rollback_on_failure": "bool=True",
        "max_task_attempts": "int=1",
        "task_timeout_seconds": "int=120",
    },
    "risky": True,
    "description": "Research with read-only sub-agents, apply a strict patch, verify it, and rollback automatically on failure.",
}


def legal_tool_names(*, include_experimental=False, include_v2=False):
    """Return tool names accepted by config and benchmark validation."""

    names = set(BASE_TOOL_SPECS)
    if include_experimental:
        names.add("delegate")
    if include_v2:
        names.add("run_task_graph")
        names.add("run_coding_workflow")
    return names

TOOL_EXAMPLES = {
    "list_files": '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
    "read_file": '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
    "search": '<tool>{"name":"search","args":{"pattern":"binary_search","path":"."}}</tool>',
    "get_file_outline": '<tool>{"name":"get_file_outline","args":{"path":"pico/runtime.py"}}</tool>',
    "find_symbol": '<tool>{"name":"find_symbol","args":{"name":"Pico","path":"pico"}}</tool>',
    "find_references": '<tool>{"name":"find_references","args":{"name":"ToolGateway","path":"pico"}}</tool>',
    "get_dependency_graph": '<tool>{"name":"get_dependency_graph","args":{"path":"pico"}}</tool>',
    "analyze_impact": '<tool>{"name":"analyze_impact","args":{"target":"ToolGateway","path":"pico","depth":1}}</tool>',
    "get_changed_files": '<tool>{"name":"get_changed_files","args":{}}</tool>',
    "run_shell": '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
    "write_file": '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
    "patch_file": '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
    "preview_diff": '<tool name="preview_diff" path="binary_search.py"><new_content>def binary_search(nums, target):\n    return mid\n</new_content></tool>',
    "apply_patch": '<tool name="apply_patch"><patch>--- a/binary_search.py\n+++ b/binary_search.py\n@@ -1,2 +1,2 @@\n def binary_search(nums, target):\n-    return -1\n+    return mid\n</patch></tool>',
    "rollback_patch": '<tool>{"name":"rollback_patch","args":{"backup_id":"patch-..."}}</tool>',
    "delegate": '<tool>{"name":"delegate","args":{"task":"inspect README.md","max_steps":3}}</tool>',
    "run_task_graph": '<tool>{"name":"run_task_graph","args":{"goal":"Map the runtime","tasks":[{"id":"outline","title":"Outline runtime","prompt":"Find the main runtime entry points."},{"id":"dependencies","title":"Inspect dependencies","prompt":"Find runtime dependencies.","depends_on":["outline"]}]}}</tool>',
    "run_coding_workflow": '<tool>{"name":"run_coding_workflow","args":{"goal":"Fix the service","research_tasks":[{"id":"inspect","title":"Inspect service","prompt":"Find the Service implementation and tests."}],"patch":"--- a/service.py\\n+++ b/service.py\\n@@ -1,2 +1,2 @@\\n class Service:\\n-    pass\\n+    def run(self): return \'ok\'\\n","verify_command":"python -m pytest -q"}}</tool>',
}


def build_tool_registry(context):
    # 工具不是动态发现的，而是显式注册的。
    # 这样模型看到的是一个有边界、可审计的动作集合。
    tools = {
        name: {**spec, "run": partial(_TOOL_RUNNERS[name], context)}
        for name, spec in BASE_TOOL_SPECS.items()
    }
    # delegate 是实验能力，不进入默认 V1 工具面；显式开启后仍受深度限制。
    if getattr(context, "enable_delegate", False) and context.depth < context.max_depth:
        tools["delegate"] = {**DELEGATE_TOOL_SPEC, "run": partial(tool_delegate, context)}
    if getattr(context, "enable_subagents", False):
        tools["run_task_graph"] = {**V2_TOOL_SPEC, "run": partial(tool_run_task_graph, context)}
        tools["run_coding_workflow"] = {
            **V2_WORKFLOW_TOOL_SPEC,
            "run": partial(tool_run_coding_workflow, context),
        }
    return tools


def tool_example(name):
    return TOOL_EXAMPLES.get(name, "")


def validate_tool(context, name, args):
    args = args or {}

    if name == "list_files":
        path = context.path(args.get("path", "."))
        if not path.is_dir():
            raise ValueError("path is not a directory")
        return

    if name == "read_file":
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        start = int(args.get("start", 1))
        end = int(args.get("end", 200))
        if start < 1 or end < start:
            raise ValueError("invalid line range")
        return

    if name == "search":
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern must not be empty")
        context.path(args.get("path", "."))
        return

    if name == "get_file_outline":
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        return

    if name in {"find_symbol", "find_references"}:
        query = str(args.get("name", "")).strip()
        if not query:
            raise ValueError("name must not be empty")
        path = context.path(args.get("path", "."))
        if not path.exists():
            raise ValueError("path does not exist")
        return

    if name == "get_dependency_graph":
        path = context.path(args.get("path", "."))
        if not path.exists():
            raise ValueError("path does not exist")
        return

    if name == "analyze_impact":
        target = str(args.get("target", "")).strip()
        if not target:
            raise ValueError("target must not be empty")
        path = context.path(args.get("path", "."))
        if not path.exists():
            raise ValueError("path does not exist")
        depth = int(args.get("depth", 1))
        if depth < 1 or depth > 2:
            raise ValueError("depth must be in [1, 2]")
        return

    if name == "get_changed_files":
        return

    if name == "run_shell":
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        return

    if name == "write_file":
        path = context.path(args["path"])
        if path.exists() and path.is_dir():
            raise ValueError("path is a directory")
        if "content" not in args:
            raise ValueError("missing content")
        return

    if name == "patch_file":
        # patch_file 故意做得很严格：old_text 必须精确命中且只能出现一次，
        # 这样修改行为才是确定的，失败原因也更容易解释。
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(args.get("old_text", ""))
        if not old_text:
            raise ValueError("old_text must not be empty")
        if "new_text" not in args:
            raise ValueError("missing new_text")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
        return

    if name == "preview_diff":
        path = context.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        if "new_content" not in args:
            raise ValueError("missing new_content")
        return

    if name == "apply_patch":
        patch = str(args.get("patch", ""))
        if not patch.strip():
            raise ValueError("patch must not be empty")
        parse_unified_diff(patch)
        return

    if name == "rollback_patch":
        if not str(args.get("backup_id", "")).strip():
            raise ValueError("backup_id must not be empty")
        return

    if name == "run_task_graph":
        tasks = args.get("tasks")
        resume = args.get("resume", False)
        if not isinstance(resume, bool):
            raise ValueError("resume must be boolean")
        if resume and not str(args.get("graph_id", "")).strip():
            raise ValueError("graph_id is required when resume is true")
        if not resume and (not isinstance(tasks, list) or not tasks):
            raise ValueError("tasks must be a non-empty list")
        if isinstance(tasks, list) and len(tasks) > 6:
            raise ValueError("at most 6 sub-agent tasks are allowed")
        if isinstance(tasks, list) and tasks:
            try:
                TaskGraph.from_mapping({"goal": args.get("goal"), "tasks": tasks})
            except TaskGraphError as exc:
                raise ValueError(str(exc)) from exc
        max_steps = int(args.get("max_steps", 4))
        if max_steps < 1 or max_steps > 12:
            raise ValueError("max_steps must be in [1, 12]")
        max_attempts = int(args.get("max_task_attempts", 1))
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_task_attempts must be in [1, 3]")
        timeout_seconds = int(args.get("task_timeout_seconds", 120))
        if timeout_seconds < 1 or timeout_seconds > 600:
            raise ValueError("task_timeout_seconds must be in [1, 600]")
        if not isinstance(args.get("isolate_worktrees", False), bool):
            raise ValueError("isolate_worktrees must be boolean")
        return

    if name == "run_coding_workflow":
        goal = str(args.get("goal", "")).strip()
        tasks = args.get("research_tasks")
        patch = str(args.get("patch", ""))
        verify_command = str(args.get("verify_command", "")).strip()
        if not goal:
            raise ValueError("goal must not be empty")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("research_tasks must be a non-empty list")
        if len(tasks) > 6:
            raise ValueError("at most 6 research tasks are allowed")
        if not patch.strip():
            raise ValueError("patch must not be empty")
        if not verify_command:
            raise ValueError("verify_command must be explicit")
        try:
            TaskGraph.from_mapping({"goal": goal, "tasks": tasks})
            parse_unified_diff(patch)
        except (TaskGraphError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        verify_timeout = int(args.get("verify_timeout", 60))
        if verify_timeout < 1 or verify_timeout > 120:
            raise ValueError("verify_timeout must be in [1, 120]")
        max_attempts = int(args.get("max_task_attempts", 1))
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_task_attempts must be in [1, 3]")
        task_timeout = int(args.get("task_timeout_seconds", 120))
        if task_timeout < 1 or task_timeout > 600:
            raise ValueError("task_timeout_seconds must be in [1, 600]")
        if not isinstance(args.get("rollback_on_failure", True), bool):
            raise ValueError("rollback_on_failure must be boolean")
        return

    if name == "delegate":
        task = str(args.get("task", "")).strip()
        if not task:
            raise ValueError("task must not be empty")
        if context.depth >= context.max_depth:
            raise ValueError("delegate depth exceeded")
        return


def tool_list_files(context, args):
    path = context.path(args.get("path", "."))
    if not path.is_dir():
        raise ValueError("path is not a directory")
    entries = [
        item for item in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        if item.name not in IGNORED_PATH_NAMES
    ]
    lines = []
    for entry in entries[:200]:
        kind = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"{kind} {entry.relative_to(context.root)}")
    return "\n".join(lines) or "(empty)"


def tool_read_file(context, args):
    path = context.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    start = int(args.get("start", 1))
    end = int(args.get("end", 200))
    if start < 1 or end < start:
        raise ValueError("invalid line range")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(f"{number:>4}: {line}" for number, line in enumerate(lines[start - 1:end], start=start))
    return f"# {path.relative_to(context.root)}\n{body}"


def tool_search(context, args):
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    path = context.path(args.get("path", "."))

    if shutil.which("rg"):
        # 优先用 rg，因为搜索会非常频繁，搜索延迟会直接影响 agent 控制循环。
        result = subprocess.run(
            ["rg", "-n", "--smart-case", "--max-count", "200", pattern, str(path)],
            cwd=context.root,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or result.stderr.strip() or "(no matches)"

    matches = []
    files = [path] if path.is_file() else [
        item for item in path.rglob("*")
        if item.is_file() and not any(part in IGNORED_PATH_NAMES for part in item.relative_to(context.root).parts)
    ]
    for file_path in files:
        for number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if pattern.lower() in line.lower():
                matches.append(f"{file_path.relative_to(context.root)}:{number}:{line}")
                if len(matches) >= 200:
                    return "\n".join(matches)
    return "\n".join(matches) or "(no matches)"


def _repo_index(context):
    index = getattr(context, "repo_index", None)
    if index is None:
        index = RepoIndex(context.root)
        context.repo_index = index
    return index


def tool_get_file_outline(context, args):
    return render_index_result(_repo_index(context).file_outline(args["path"]))


def tool_find_symbol(context, args):
    return render_index_result(
        _repo_index(context).find_symbol(args["name"], args.get("path", "."))
    )


def tool_find_references(context, args):
    return render_index_result(
        _repo_index(context).find_references(args["name"], args.get("path", "."))
    )


def tool_get_dependency_graph(context, args):
    return render_index_result(
        _repo_index(context).dependency_graph(args.get("path", "."))
    )


def tool_analyze_impact(context, args):
    return render_index_result(
        _repo_index(context).analyze_impact(
            args["target"],
            args.get("path", "."),
            args.get("depth", 1),
        )
    )


def tool_get_changed_files(context, args):
    return render_index_result(_repo_index(context).changed_files())


def tool_run_shell(context, args):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError("command must not be empty")
    timeout = int(args.get("timeout", 20))
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be in [1, 120]")
    backend = context.execution_backend
    if backend is None or getattr(backend, "mode", "host") == "host":
        # Keep the historical injection point ``pico.tools.subprocess.run``
        # observable for callers/tests while retaining the backend contract.
        backend = HostExecutionBackend(
            runner=run_shell_command,
            subprocess_runner=subprocess.run,
        )
    result = backend.run(
        command,
        cwd=context.root,
        timeout=timeout,
        # 这里传入的是过滤后的环境变量，而不是直接继承整个父 shell 环境，
        # 目的是减少敏感信息被意外带进命令执行环境的风险。
        env=context.shell_env(),
    )
    return textwrap.dedent(
        f"""\
        exit_code: {result.returncode}
        execution_backend: {result.execution_backend}
        sandbox_mode: {result.sandbox_mode}
        timeout_killed: {str(result.timeout_killed).lower()}
        oom_killed: {str(result.oom_killed).lower()}
        resource_limit_reason: {result.resource_limit_reason or "none"}
        stdout:
        {result.stdout.strip() or "(empty)"}
        stderr:
        {result.stderr.strip() or "(empty)"}
        """
    ).strip()


def tool_write_file(context, args):
    path = context.path(args["path"])
    content = str(args["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {path.relative_to(context.root)} ({len(content)} chars)"


def tool_patch_file(context, args):
    path = context.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    old_text = str(args.get("old_text", ""))
    if not old_text:
        raise ValueError("old_text must not be empty")
    if "new_text" not in args:
        raise ValueError("missing new_text")
    text = path.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count != 1:
        raise ValueError(f"old_text must occur exactly once, found {count}")
    path.write_text(text.replace(old_text, str(args["new_text"]), 1), encoding="utf-8")
    return f"patched {path.relative_to(context.root)}"


def tool_preview_diff(context, args):
    return render_index_result(
        preview_file_diff(context.root, args["path"], args["new_content"])
    )


def tool_apply_patch(context, args):
    journal = getattr(context, "patch_journal", None) or PatchJournal(context.root)
    result = apply_unified_diff(context.root, args["patch"], journal=journal)
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def tool_rollback_patch(context, args):
    journal = getattr(context, "patch_journal", None) or PatchJournal(context.root)
    result = journal.rollback(args["backup_id"])
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def tool_run_task_graph(context, args):
    callback = getattr(context, "spawn_subagents", None)
    if callback is None:
        raise ValueError("sub-agent supervisor is not configured")
    return callback(args)


def tool_run_coding_workflow(context, args):
    callback = getattr(context, "spawn_coding_workflow", None)
    if callback is None:
        raise ValueError("coding workflow is not configured")
    return callback(args)


def tool_delegate(context, args):
    if context.depth >= context.max_depth:
        raise ValueError("delegate depth exceeded")
    task = str(args.get("task", "")).strip()
    if not task:
        raise ValueError("task must not be empty")
    return context.spawn_delegate(args)


_TOOL_RUNNERS = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "search": tool_search,
    "get_file_outline": tool_get_file_outline,
    "find_symbol": tool_find_symbol,
    "find_references": tool_find_references,
    "get_dependency_graph": tool_get_dependency_graph,
    "analyze_impact": tool_analyze_impact,
    "get_changed_files": tool_get_changed_files,
    "run_shell": tool_run_shell,
    "write_file": tool_write_file,
    "patch_file": tool_patch_file,
    "preview_diff": tool_preview_diff,
    "apply_patch": tool_apply_patch,
    "rollback_patch": tool_rollback_patch,
    "run_task_graph": tool_run_task_graph,
    "run_coding_workflow": tool_run_coding_workflow,
}

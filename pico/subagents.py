"""Bounded read-only sub-agent orchestration for Pico V2."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
from pathlib import Path
from uuid import uuid4

from .atomic_io import replace_with_retry
from .evidence import EvidenceBundle, aggregate_evidence, dependency_context
from .run_store import RunStore
from .session_store import SessionStore
from .task_graph import (
    DEFAULT_TASK_MAX_ATTEMPTS,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    MAX_TASK_ATTEMPTS,
    MAX_TASK_TIMEOUT_SECONDS,
    TASK_COMPLETED,
    TaskGraph,
    TaskGraphError,
)
from .workspace import WorkspaceContext, clip
from .workspace_isolation import WorkspaceIsolationError, WorkspaceLease


MAX_SUBAGENT_TASKS = 6
DEFAULT_SUBAGENT_STEPS = 4
MAX_SUBAGENT_GRAPH_STEPS = 12
READ_ONLY_SUBAGENT_TOOLS = (
    "list_files",
    "read_file",
    "search",
    "get_file_outline",
    "find_symbol",
    "find_references",
    "get_dependency_graph",
    "get_changed_files",
    "preview_diff",
)


class SubAgentTimeoutError(TimeoutError):
    """Raised when a child runtime exceeds its task budget."""


def _bounded_json(payload, limit=3600):
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if len(rendered) <= limit:
        return rendered
    summary = {
        "graph_id": payload.get("graph_id", ""),
        "status": payload.get("status", ""),
        "summary": clip(payload.get("summary", ""), 900),
        "findings": [clip(item, 320) for item in payload.get("findings", [])[:8]],
        "risks": [clip(item, 320) for item in payload.get("risks", [])[:8]],
        "recommendations": [clip(item, 320) for item in payload.get("recommendations", [])[:8]],
        "evidence": payload.get("evidence", [])[:8],
        "confidence": payload.get("confidence", 0.0),
        "isolation_mode": payload.get("isolation_mode", ""),
        "artifact_dir": payload.get("artifact_dir", ""),
        "tasks": [
            {
                "task_id": item.get("task_id", ""),
                "status": item.get("status", ""),
                "result": clip(item.get("result", ""), 420),
                "error": clip(item.get("error", ""), 240),
                "attempts": item.get("attempts", 0),
                "evidence": item.get("evidence", {}),
            }
            for item in payload.get("tasks", [])
        ],
        "truncated": True,
        "message": "Full sub-agent graph state is stored in the artifact directory.",
    }
    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)


class SubAgentManager:
    """Schedule independent research tasks and persist their evidence."""

    def __init__(self, agent):
        self.agent = agent

    def _artifact_root(self, graph):
        return self._artifact_root_for_id(graph.graph_id)

    def _artifact_root_for_id(self, graph_id):
        current_run_dir = getattr(self.agent, "current_run_dir", None)
        if current_run_dir:
            root = Path(current_run_dir) / "subagents" / str(graph_id)
        else:
            session_id = str(self.agent.session.get("id", "session"))
            root = Path(self.agent.root) / ".pico" / "subagents" / session_id / str(graph_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _emit(self, event, payload):
        task_state = getattr(self.agent, "current_task_state", None)
        if task_state is not None:
            self.agent.emit_trace(task_state, event, payload)

    @staticmethod
    def _write_state(path, graph, metadata, summary=None):
        payload = {"graph": graph.to_dict(), "metadata": metadata}
        if summary is not None:
            payload["summary"] = summary
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            replace_with_retry(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _write_evidence(path, evidence):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            replace_with_retry(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _workspace_for_task(self, artifact_root, isolate_worktrees):
        if not isolate_worktrees:
            return Path(self.agent.root), None, "read_only_shared_workspace", ""
        try:
            lease = WorkspaceLease.create(
                self.agent.root,
                base_dir=artifact_root / "worktrees",
            )
            return lease.workspace_root, lease, "detached_git_worktree_read_only", ""
        except (WorkspaceIsolationError, OSError) as exc:
            return (
                Path(self.agent.root),
                None,
                "read_only_shared_workspace",
                f"worktree isolation unavailable: {exc}",
            )

    def _child(self, workspace_root, task_dir, max_steps):
        # Import lazily to keep the runtime facade and sub-agent manager acyclic.
        from .runtime import Pico

        workspace = WorkspaceContext.build(workspace_root)
        return Pico(
            model_client=self.agent.model_client,
            workspace=workspace,
            session_store=SessionStore(task_dir / "session"),
            run_store=RunStore(task_dir / "runs"),
            approval_policy="never",
            max_steps=max_steps,
            max_new_tokens=self.agent.max_new_tokens,
            depth=self.agent.depth + 1,
            max_depth=self.agent.max_depth,
            enable_delegate=False,
            enable_subagents=False,
            auto_promote_memory=False,
            read_only=True,
            secret_env_names=self.agent.secret_env_names,
            shell_env_allowlist=self.agent.shell_env_allowlist,
            skill_paths=(Path(workspace_root) / ".pico" / "skills",),
            plan_mode=True,
            verify_command="",
            verify_timeout=self.agent.verify_timeout,
            max_verification_attempts=0,
            allowed_tools=READ_ONLY_SUBAGENT_TOOLS,
        )

    @staticmethod
    def _task_prompt(task, completed):
        dependency_text = ""
        if completed:
            dependency_text = "\n\nStructured evidence from completed dependencies:\n" + dependency_context(
                [
                    result if isinstance(result, EvidenceBundle) else EvidenceBundle.from_dict(result, task_id)
                    for task_id, result in completed.items()
                ]
            )
        return (
            "You are a bounded read-only research sub-agent in Pico V2. "
            "Do not modify files, run risky tools, or claim work you did not verify. "
            "Use Repo Index before broad reads when possible. Return one <final> whose body is valid JSON. "
            "The JSON schema is: {\"summary\":\"...\",\"findings\":[\"...\"],"
            "\"evidence\":[{\"path\":\"relative/path.py\",\"line_start\":1,"
            "\"line_end\":2,\"symbol\":\"...\",\"claim\":\"...\","
            "\"confidence\":0.8}],\"risks\":[\"...\"],"
            "\"recommendations\":[\"...\"],\"confidence\":0.8}.\n\n"
            f"Task: {task.title}\n"
            f"Role: {task.role}\n"
            f"Request: {task.prompt}"
            f"{dependency_text}"
        )

    @staticmethod
    def _ask_with_timeout(child, prompt, timeout_seconds):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pico-subagent")
        future = executor.submit(child.ask, prompt)
        try:
            answer = future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise SubAgentTimeoutError(
                f"sub-agent exceeded timeout of {timeout_seconds}s"
            ) from exc
        except Exception:
            executor.shutdown(wait=True)
            raise
        else:
            executor.shutdown(wait=True)
            return answer

    def _load_or_create_graph(self, args):
        resume = bool(args.get("resume", False))
        graph_id = str(args.get("graph_id", "")).strip()
        if resume:
            if not graph_id:
                raise TaskGraphError("graph_id is required when resume is true")
            artifact_root = self._artifact_root_for_id(graph_id)
            state_path = artifact_root / "task_graph.json"
            if not state_path.is_file():
                raise TaskGraphError(f"task graph checkpoint not found: {graph_id}")
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                graph = TaskGraph.from_dict(payload.get("graph", {}))
            except (OSError, ValueError, TypeError) as exc:
                raise TaskGraphError(f"could not load task graph checkpoint: {graph_id}") from exc
            metadata = dict(payload.get("metadata", {}) or {})
            metadata["resume_count"] = int(metadata.get("resume_count", 0)) + 1
            metadata["recovered_tasks"] = graph.recover_running_tasks()
            return graph, metadata, artifact_root, True

        raw_tasks = args.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise TaskGraphError("task graph must contain a non-empty tasks list")
        if len(raw_tasks) > MAX_SUBAGENT_TASKS:
            raise TaskGraphError(f"at most {MAX_SUBAGENT_TASKS} sub-agent tasks are allowed")
        try:
            max_task_attempts = int(args.get("max_task_attempts", DEFAULT_TASK_MAX_ATTEMPTS))
            task_timeout_seconds = int(args.get("task_timeout_seconds", DEFAULT_TASK_TIMEOUT_SECONDS))
        except (TypeError, ValueError) as exc:
            raise TaskGraphError("invalid task retry or timeout budget") from exc
        if max_task_attempts < 1 or max_task_attempts > MAX_TASK_ATTEMPTS:
            raise TaskGraphError(f"max_task_attempts must be in [1, {MAX_TASK_ATTEMPTS}]")
        if task_timeout_seconds < 1 or task_timeout_seconds > MAX_TASK_TIMEOUT_SECONDS:
            raise TaskGraphError(
                f"task_timeout_seconds must be in [1, {MAX_TASK_TIMEOUT_SECONDS}]"
            )
        graph = TaskGraph.from_mapping(
            {
                "graph_id": graph_id,
                "goal": args.get("goal"),
                "tasks": raw_tasks,
            },
            default_max_attempts=max_task_attempts,
            default_timeout_seconds=task_timeout_seconds,
        )
        artifact_root = self._artifact_root(graph)
        metadata = {
            "goal": graph.goal,
            "max_task_attempts": max_task_attempts,
            "task_timeout_seconds": task_timeout_seconds,
            "resume_count": 0,
            "recovered_tasks": [],
        }
        return graph, metadata, artifact_root, False

    def execute(self, args):
        args = dict(args or {})
        graph, metadata, artifact_root, resumed = self._load_or_create_graph(args)
        max_steps = int(args.get("max_steps", DEFAULT_SUBAGENT_STEPS))
        if max_steps < 1 or max_steps > MAX_SUBAGENT_GRAPH_STEPS:
            raise TaskGraphError("max_steps must be in [1, 12]")
        isolate_worktrees = args.get("isolate_worktrees", False)
        if not isinstance(isolate_worktrees, bool):
            raise TaskGraphError("isolate_worktrees must be boolean")
        state_path = artifact_root / "task_graph.json"
        metadata.update(
            {
                "max_steps": max_steps,
                "isolation_requested": isolate_worktrees,
                "isolation_mode": metadata.get("isolation_mode", ""),
                "isolation_fallback_reason": metadata.get("isolation_fallback_reason", ""),
                "read_only_tools": list(READ_ONLY_SUBAGENT_TOOLS),
                "resumed": resumed,
            }
        )
        completed_results = {
            task.task_id: EvidenceBundle.from_dict(task.evidence, task_id=task.task_id, workspace_root=self.agent.root)
            for task in graph.tasks
            if task.status == TASK_COMPLETED and task.evidence
        }
        self._write_state(state_path, graph, metadata)

        while not graph.is_terminal():
            ready = list(graph.ready_tasks())
            if not ready:
                graph.block_dependents()
                if not graph.is_terminal():
                    raise TaskGraphError("task graph has pending tasks but no schedulable task")
                break

            # V2.1 remains serial by design. Recovery, retry and evidence ordering
            # are deterministic before a later bounded-concurrency scheduler.
            for task in ready:
                graph.mark_running(task.task_id)
                task_dir = artifact_root / task.task_id
                task_dir.mkdir(parents=True, exist_ok=True)
                workspace_root, lease, isolation_mode, fallback_reason = self._workspace_for_task(
                    artifact_root / task.task_id,
                    isolate_worktrees,
                )
                metadata["isolation_mode"] = isolation_mode
                if fallback_reason and not metadata["isolation_fallback_reason"]:
                    metadata["isolation_fallback_reason"] = fallback_reason
                self._emit(
                    "subagent_started",
                    {
                        "graph_id": graph.graph_id,
                        "subagent_task_id": task.task_id,
                        "depends_on": list(task.depends_on),
                        "isolation_mode": isolation_mode,
                        "attempt": task.attempts,
                        "max_attempts": task.max_attempts,
                        "timeout_seconds": task.timeout_seconds,
                    },
                )
                child = None
                timed_out = False
                try:
                    child = self._child(workspace_root, task_dir, max_steps)
                    answer = self._ask_with_timeout(
                        child,
                        self._task_prompt(task, completed_results),
                        task.timeout_seconds,
                    )
                    child_status = getattr(getattr(child, "current_task_state", None), "status", "completed")
                    run_dir = str(getattr(child, "current_run_dir", "") or "")
                    if child_status != "completed":
                        raise RuntimeError(f"child stopped with status {child_status}: {answer}")
                    evidence = EvidenceBundle.from_answer(
                        answer,
                        task_id=task.task_id,
                        workspace_root=workspace_root,
                    )
                    graph.mark_completed(
                        task.task_id,
                        evidence.summary,
                        run_dir=run_dir,
                        evidence=evidence.to_dict(),
                    )
                    completed_results[task.task_id] = evidence
                    self._write_evidence(task_dir / "evidence.json", evidence.to_dict())
                    self._emit(
                        "subagent_finished",
                        {
                            "graph_id": graph.graph_id,
                            "subagent_task_id": task.task_id,
                            "status": "completed",
                            "run_dir": run_dir,
                            "isolation_mode": isolation_mode,
                            "attempt": task.attempts,
                            "evidence": evidence.to_dict(),
                        },
                    )
                except Exception as exc:
                    timed_out = isinstance(exc, SubAgentTimeoutError)
                    run_dir = str(getattr(child, "current_run_dir", "") or "")
                    error = str(exc) or exc.__class__.__name__
                    if task.can_retry():
                        graph.mark_retry(
                            task.task_id,
                            error,
                            reason="timeout" if timed_out else "child_failure",
                        )
                        self._emit(
                            "subagent_retry",
                            {
                                "graph_id": graph.graph_id,
                                "subagent_task_id": task.task_id,
                                "attempt": task.attempts,
                                "next_attempt": task.attempts + 1,
                                "reason": "timeout" if timed_out else "child_failure",
                                "error": clip(error, 500),
                            },
                        )
                    else:
                        graph.mark_failed(task.task_id, error, run_dir=run_dir)
                    self._emit(
                        "subagent_finished",
                        {
                            "graph_id": graph.graph_id,
                            "subagent_task_id": task.task_id,
                            "status": "retrying" if task.status != "failed" else "failed",
                            "error": clip(error, 500),
                            "run_dir": run_dir,
                            "isolation_mode": isolation_mode,
                            "attempt": task.attempts,
                            "timeout": timed_out,
                        },
                    )
                finally:
                    if lease is not None:
                        try:
                            if lease.status() == "clean":
                                lease.remove()
                        except Exception as exc:
                            if not metadata["isolation_fallback_reason"]:
                                metadata["isolation_fallback_reason"] = f"worktree cleanup deferred: {exc}"
                    if child is not None and not timed_out:
                        child.close()
                self._write_state(state_path, graph, metadata)

        bundles = [
            EvidenceBundle.from_dict(
                task.evidence,
                task_id=task.task_id,
                workspace_root=self.agent.root,
            )
            for task in graph.tasks
            if task.status == TASK_COMPLETED and task.evidence
        ]
        summary = aggregate_evidence(bundles, goal=graph.goal)
        summary.update(
            {
                "task_count": len(graph.tasks),
                "completed_count": sum(task.status == TASK_COMPLETED for task in graph.tasks),
                "failed_count": sum(task.status == "failed" for task in graph.tasks),
                "blocked_count": sum(task.status == "blocked" for task in graph.tasks),
            }
        )
        self._write_state(state_path, graph, metadata, summary=summary)
        tasks = [
            {
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status,
                "result": task.result,
                "error": task.error,
                "depends_on": list(task.depends_on),
                "run_dir": task.run_dir,
                "attempts": task.attempts,
                "max_attempts": task.max_attempts,
                "timeout_seconds": task.timeout_seconds,
                "retry_history": list(task.retry_history),
                "evidence": dict(task.evidence),
            }
            for task in graph.tasks
        ]
        return _bounded_json(
            {
                "graph_id": graph.graph_id,
                "goal": graph.goal,
                "status": graph.status,
                "resumed": resumed,
                "recovered_tasks": metadata.get("recovered_tasks", []),
                **summary,
                "isolation_mode": metadata["isolation_mode"] or "read_only_shared_workspace",
                "isolation_fallback_reason": metadata["isolation_fallback_reason"],
                "artifact_dir": str(artifact_root),
                "tasks": tasks,
            }
        )

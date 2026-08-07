"""Validated dependency graph primitives for the V2 supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


TASK_GRAPH_SCHEMA_VERSION = "task-graph-v2"
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_BLOCKED = "blocked"
TERMINAL_TASK_STATUSES = {TASK_COMPLETED, TASK_FAILED, TASK_BLOCKED}
DEFAULT_TASK_MAX_ATTEMPTS = 1
DEFAULT_TASK_TIMEOUT_SECONDS = 120
MAX_TASK_ATTEMPTS = 3
MAX_TASK_TIMEOUT_SECONDS = 600


class TaskGraphError(ValueError):
    """Raised when a task graph cannot be safely scheduled."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, default=""):
    text = str(value if value is not None else default).strip()
    return text or str(default)


@dataclass
class GraphTask:
    task_id: str
    title: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    role: str = "researcher"
    status: str = TASK_PENDING
    attempts: int = 0
    result: str = ""
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    run_dir: str = ""
    max_attempts: int = DEFAULT_TASK_MAX_ATTEMPTS
    timeout_seconds: int = DEFAULT_TASK_TIMEOUT_SECONDS
    evidence: dict = field(default_factory=dict)
    retry_history: list[dict] = field(default_factory=list)

    @classmethod
    def from_mapping(
        cls,
        value,
        fallback_id,
        default_max_attempts=DEFAULT_TASK_MAX_ATTEMPTS,
        default_timeout_seconds=DEFAULT_TASK_TIMEOUT_SECONDS,
    ):
        if not isinstance(value, dict):
            raise TaskGraphError("task entries must be objects")
        raw_dependencies = value.get("depends_on", value.get("dependencies", []))
        if isinstance(raw_dependencies, str):
            raw_dependencies = [raw_dependencies]
        if not isinstance(raw_dependencies, list):
            raise TaskGraphError(f"depends_on must be a list for task {fallback_id}")
        task_id = _text(value.get("id", value.get("task_id")), fallback_id)
        prompt = _text(value.get("prompt", value.get("description")), "Inspect the repository and report evidence.")
        try:
            max_attempts = int(value.get("max_attempts", default_max_attempts))
            timeout_seconds = int(value.get("timeout_seconds", default_timeout_seconds))
        except (TypeError, ValueError) as exc:
            raise TaskGraphError(f"invalid execution limits for task {task_id}") from exc
        return cls(
            task_id=task_id,
            title=_text(value.get("title"), task_id),
            prompt=prompt,
            depends_on=[_text(item) for item in raw_dependencies if _text(item)],
            role=_text(value.get("role"), "researcher"),
            max_attempts=max_attempts,
            timeout_seconds=timeout_seconds,
            evidence=dict(value.get("evidence", {}) or {}) if isinstance(value.get("evidence", {}), dict) else {},
            retry_history=list(value.get("retry_history", []) or []),
        )

    @classmethod
    def from_dict(cls, value):
        value = dict(value or {})
        task = cls.from_mapping(
            value,
            value.get("task_id", "task"),
            default_max_attempts=value.get("max_attempts", DEFAULT_TASK_MAX_ATTEMPTS),
            default_timeout_seconds=value.get("timeout_seconds", DEFAULT_TASK_TIMEOUT_SECONDS),
        )
        task.status = _text(value.get("status"), TASK_PENDING)
        task.attempts = max(0, int(value.get("attempts", 0)))
        task.result = _text(value.get("result"), "")
        task.error = _text(value.get("error"), "")
        task.started_at = _text(value.get("started_at"), "")
        task.finished_at = _text(value.get("finished_at"), "")
        task.run_dir = _text(value.get("run_dir"), "")
        task.evidence = dict(value.get("evidence", {}) or {}) if isinstance(value.get("evidence", {}), dict) else {}
        task.retry_history = list(value.get("retry_history", []) or [])
        return task

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "title": self.title,
            "prompt": self.prompt,
            "depends_on": list(self.depends_on),
            "role": self.role,
            "status": self.status,
            "attempts": self.attempts,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_dir": self.run_dir,
            "max_attempts": self.max_attempts,
            "timeout_seconds": self.timeout_seconds,
            "evidence": dict(self.evidence),
            "retry_history": list(self.retry_history),
        }

    def can_retry(self):
        return self.attempts < self.max_attempts


@dataclass
class TaskGraph:
    graph_id: str
    goal: str
    tasks: list[GraphTask]
    status: str = "active"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    schema_version: str = TASK_GRAPH_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        value,
        default_max_attempts=DEFAULT_TASK_MAX_ATTEMPTS,
        default_timeout_seconds=DEFAULT_TASK_TIMEOUT_SECONDS,
    ):
        value = dict(value or {})
        raw_tasks = value.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise TaskGraphError("task graph must contain a non-empty tasks list")
        tasks = [
            item
            if isinstance(item, GraphTask)
            else GraphTask.from_mapping(
                item,
                f"task_{index}",
                default_max_attempts=default_max_attempts,
                default_timeout_seconds=default_timeout_seconds,
            )
            for index, item in enumerate(raw_tasks, start=1)
        ]
        graph = cls(
            graph_id=_text(value.get("graph_id"), "graph_" + uuid4().hex[:8]),
            goal=_text(value.get("goal"), "Complete the delegated research tasks."),
            tasks=tasks,
        )
        graph.validate()
        return graph

    @classmethod
    def from_dict(cls, value):
        value = dict(value or {})
        raw_tasks = value.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise TaskGraphError("task graph must contain a non-empty tasks list")
        graph = cls(
            graph_id=_text(value.get("graph_id"), "graph_" + uuid4().hex[:8]),
            goal=_text(value.get("goal"), "Complete the delegated research tasks."),
            tasks=[GraphTask.from_dict(item) for item in raw_tasks],
            status=_text(value.get("status"), "active"),
            created_at=_text(value.get("created_at"), _now()),
            updated_at=_text(value.get("updated_at"), _now()),
            schema_version=_text(value.get("schema_version"), TASK_GRAPH_SCHEMA_VERSION),
        )
        graph.validate()
        graph._refresh_status()
        return graph

    def validate(self):
        ids = [task.task_id for task in self.tasks]
        if any(not task_id for task_id in ids):
            raise TaskGraphError("task id must not be empty")
        if len(set(ids)) != len(ids):
            raise TaskGraphError("task ids must be unique")
        task_ids = set(ids)
        for task in self.tasks:
            if task.status not in {TASK_PENDING, TASK_RUNNING, *TERMINAL_TASK_STATUSES}:
                raise TaskGraphError(f"invalid task status for {task.task_id}: {task.status}")
            if task.max_attempts < 1 or task.max_attempts > MAX_TASK_ATTEMPTS:
                raise TaskGraphError(
                    f"max_attempts must be in [1, {MAX_TASK_ATTEMPTS}] for task {task.task_id}"
                )
            if task.timeout_seconds < 1 or task.timeout_seconds > MAX_TASK_TIMEOUT_SECONDS:
                raise TaskGraphError(
                    f"timeout_seconds must be in [1, {MAX_TASK_TIMEOUT_SECONDS}] for task {task.task_id}"
                )
            if task.task_id in task.depends_on:
                raise TaskGraphError(f"task cannot depend on itself: {task.task_id}")
            unknown = [dependency for dependency in task.depends_on if dependency not in task_ids]
            if unknown:
                raise TaskGraphError(
                    f"task {task.task_id} depends on unknown task(s): {', '.join(unknown)}"
                )

        visiting = set()
        visited = set()

        def visit(task_id):
            if task_id in visiting:
                raise TaskGraphError("task graph contains a dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            task = next(item for item in self.tasks if item.task_id == task_id)
            for dependency in task.depends_on:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)
        return self

    def task(self, task_id):
        return next((task for task in self.tasks if task.task_id == str(task_id)), None)

    def ready_tasks(self):
        completed = {task.task_id for task in self.tasks if task.status == TASK_COMPLETED}
        return [
            task
            for task in self.tasks
            if task.status == TASK_PENDING and all(dependency in completed for dependency in task.depends_on)
        ]

    def mark_running(self, task_id):
        task = self.task(task_id)
        if task is None:
            raise TaskGraphError(f"unknown task: {task_id}")
        if task.status != TASK_PENDING:
            raise TaskGraphError(f"task is not pending: {task_id}")
        if task not in self.ready_tasks():
            raise TaskGraphError(f"task dependencies are not complete: {task_id}")
        task.status = TASK_RUNNING
        task.attempts += 1
        task.started_at = _now()
        self.updated_at = _now()
        return task

    def mark_completed(self, task_id, result="", run_dir="", evidence=None):
        task = self.task(task_id)
        if task is None:
            raise TaskGraphError(f"unknown task: {task_id}")
        task.status = TASK_COMPLETED
        task.result = _text(result)
        task.error = ""
        task.run_dir = _text(run_dir)
        task.evidence = dict(evidence or {})
        task.finished_at = _now()
        self.updated_at = _now()
        self._refresh_status()
        return task

    def mark_failed(self, task_id, error, run_dir=""):
        task = self.task(task_id)
        if task is None:
            raise TaskGraphError(f"unknown task: {task_id}")
        task.status = TASK_FAILED
        task.error = _text(error, "sub-agent failed")
        task.run_dir = _text(run_dir)
        task.finished_at = _now()
        self.updated_at = _now()
        self.block_dependents()
        self._refresh_status()
        return task

    def mark_retry(self, task_id, error, reason="retryable_failure"):
        task = self.task(task_id)
        if task is None:
            raise TaskGraphError(f"unknown task: {task_id}")
        if task.status != TASK_RUNNING:
            raise TaskGraphError(f"task is not running: {task_id}")
        task.retry_history.append(
            {
                "attempt": task.attempts,
                "error": _text(error, "sub-agent failed"),
                "reason": _text(reason, "retryable_failure"),
                "created_at": _now(),
            }
        )
        task.status = TASK_PENDING
        task.error = _text(error, "sub-agent failed")
        task.finished_at = _now()
        self.status = "active"
        self.updated_at = _now()
        return task

    def recover_running_tasks(self):
        """Requeue interrupted tasks, or fail them when their budget is spent."""

        recovered = []
        for task in self.tasks:
            if task.status != TASK_RUNNING:
                continue
            error = "recovered interrupted running task"
            if task.can_retry():
                task.retry_history.append(
                    {
                        "attempt": task.attempts,
                        "error": error,
                        "reason": "graph_resume",
                        "created_at": _now(),
                    }
                )
                task.status = TASK_PENDING
                task.error = error
                task.finished_at = _now()
                recovered.append(task.task_id)
            else:
                task.status = TASK_FAILED
                task.error = "task interrupted after exhausting retry budget"
                task.finished_at = _now()
                recovered.append(task.task_id)
        self.block_dependents()
        self._refresh_status()
        return recovered

    def block_dependents(self):
        changed = True
        failed = {TASK_FAILED, TASK_BLOCKED}
        while changed:
            changed = False
            for task in self.tasks:
                if task.status != TASK_PENDING:
                    continue
                dependency_statuses = {
                    dependency_task.status
                    for dependency in task.depends_on
                    if (dependency_task := self.task(dependency)) is not None
                }
                if dependency_statuses & failed:
                    task.status = TASK_BLOCKED
                    task.error = "blocked by a failed dependency"
                    task.finished_at = _now()
                    changed = True
        self.updated_at = _now()
        return self

    def _refresh_status(self):
        if all(task.status in TERMINAL_TASK_STATUSES for task in self.tasks):
            self.status = "failed" if any(task.status == TASK_FAILED for task in self.tasks) else "completed"
        else:
            self.status = "active"
        self.updated_at = _now()
        return self

    def is_terminal(self):
        return all(task.status in TERMINAL_TASK_STATUSES for task in self.tasks)

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "goal": self.goal,
            "status": self.status,
            "tasks": [task.to_dict() for task in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

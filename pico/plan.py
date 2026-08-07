"""Structured execution plans for the V1.5 agent harness.

The plan is deliberately small and serial.  V1.5 does not pretend to be a
multi-agent scheduler; it gives one agent an explicit, resumable work queue
that can be shown in prompts, checkpoints, traces, and reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


PLAN_SCHEMA_VERSION = "plan-v1.5"

PLAN_ACTIVE = "active"
PLAN_COMPLETED = "completed"
PLAN_BLOCKED = "blocked"
PLAN_FAILED = "failed"

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_BLOCKED = "blocked"
TASK_FAILED = "failed"
TASK_SKIPPED = "skipped"
TERMINAL_TASK_STATUSES = {TASK_COMPLETED, TASK_BLOCKED, TASK_FAILED, TASK_SKIPPED}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, default=""):
    text = str(value if value is not None else default).strip()
    return text or str(default)


@dataclass
class PlanTask:
    task_id: str
    title: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: str = TASK_PENDING
    attempts: int = 0
    tool_steps: int = 0
    last_action: str = ""
    result: str = ""
    blocker: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, data, fallback_id="task_1"):
        data = dict(data or {})
        depends_on = data.get("depends_on", data.get("dependencies", []))
        if isinstance(depends_on, str):
            depends_on = [depends_on]
        if not isinstance(depends_on, list):
            depends_on = []
        status = _text(data.get("status"), TASK_PENDING)
        if status not in {TASK_PENDING, TASK_RUNNING, *TERMINAL_TASK_STATUSES}:
            status = TASK_PENDING
        return cls(
            task_id=_text(data.get("task_id", data.get("id")), fallback_id),
            title=_text(data.get("title", data.get("name")), "Execute task"),
            description=_text(data.get("description")),
            depends_on=[_text(item) for item in depends_on if _text(item)],
            status=status,
            attempts=int(data.get("attempts", 0) or 0),
            tool_steps=int(data.get("tool_steps", 0) or 0),
            last_action=_text(data.get("last_action")),
            result=_text(data.get("result")),
            blocker=_text(data.get("blocker")),
            created_at=_text(data.get("created_at"), _now()),
            updated_at=_text(data.get("updated_at"), _now()),
        )

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "attempts": self.attempts,
            "tool_steps": self.tool_steps,
            "last_action": self.last_action,
            "result": self.result,
            "blocker": self.blocker,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def touch(self):
        self.updated_at = _now()
        return self


@dataclass
class PlanState:
    plan_id: str
    goal: str
    tasks: list[PlanTask] = field(default_factory=list)
    status: str = PLAN_ACTIVE
    current_task_id: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    schema_version: str = PLAN_SCHEMA_VERSION

    @classmethod
    def create(cls, goal, tasks=None, plan_id=""):
        plan_id = _text(plan_id) or "plan_" + uuid4().hex[:8]
        goal = _text(goal, "Complete the user request")
        normalized = []
        for index, item in enumerate(tasks or [], start=1):
            normalized.append(item if isinstance(item, PlanTask) else PlanTask.from_dict(item, f"task_{index}"))
        if not normalized:
            normalized = [PlanTask(task_id="task_1", title="Execute user request", description=goal)]
        plan = cls(plan_id=plan_id, goal=goal, tasks=normalized)
        plan._normalize()
        return plan

    @classmethod
    def from_dict(cls, data, fallback_goal="Complete the user request"):
        data = dict(data or {})
        raw_tasks = data.get("tasks", [])
        if not isinstance(raw_tasks, list):
            raw_tasks = []
        tasks = [PlanTask.from_dict(item, f"task_{index}") for index, item in enumerate(raw_tasks, start=1)]
        plan = cls(
            plan_id=_text(data.get("plan_id"), "plan_" + uuid4().hex[:8]),
            goal=_text(data.get("goal"), fallback_goal),
            tasks=tasks,
            status=_text(data.get("status"), PLAN_ACTIVE),
            current_task_id=_text(data.get("current_task_id")),
            created_at=_text(data.get("created_at"), _now()),
            updated_at=_text(data.get("updated_at"), _now()),
            schema_version=_text(data.get("schema_version"), PLAN_SCHEMA_VERSION),
        )
        if plan.status not in {PLAN_ACTIVE, PLAN_COMPLETED, PLAN_BLOCKED, PLAN_FAILED}:
            plan.status = PLAN_ACTIVE
        plan._normalize()
        return plan

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "goal": self.goal,
            "status": self.status,
            "current_task_id": self.current_task_id,
            "tasks": [task.to_dict() for task in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def touch(self):
        self.updated_at = _now()
        return self

    def _normalize(self):
        unique = []
        seen = set()
        for index, task in enumerate(self.tasks, start=1):
            if not isinstance(task, PlanTask):
                task = PlanTask.from_dict(task, f"task_{index}")
            if not task.task_id or task.task_id in seen:
                task.task_id = f"task_{index}"
            seen.add(task.task_id)
            task.depends_on = [dependency for dependency in task.depends_on if dependency in seen or dependency != task.task_id]
            unique.append(task)
        self.tasks = unique or [PlanTask(task_id="task_1", title="Execute user request", description=self.goal)]
        ids = {task.task_id for task in self.tasks}
        for task in self.tasks:
            task.depends_on = [dependency for dependency in task.depends_on if dependency in ids and dependency != task.task_id]
        if self.current_task_id not in ids:
            self.current_task_id = ""
        if not self.current_task_id:
            next_task = self.next_task()
            self.current_task_id = next_task.task_id if next_task else self.tasks[-1].task_id
        self.updated_at = _now()
        return self

    def current_task(self):
        for task in self.tasks:
            if task.task_id == self.current_task_id:
                return task
        return None

    def _dependencies_complete(self, task):
        completed = {item.task_id for item in self.tasks if item.status in {TASK_COMPLETED, TASK_SKIPPED}}
        return all(dependency in completed for dependency in task.depends_on)

    def next_task(self):
        for task in self.tasks:
            if task.status == TASK_RUNNING:
                return task
            if task.status == TASK_PENDING and self._dependencies_complete(task):
                return task
        return None

    def apply_model_plan(self, payload):
        payload = dict(payload or {})
        raw_tasks = payload.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ValueError("plan must contain a non-empty tasks list")
        tasks = []
        for index, item in enumerate(raw_tasks, start=1):
            if not isinstance(item, dict):
                raise ValueError("plan tasks must be objects")
            tasks.append(PlanTask.from_dict(item, f"task_{index}"))
        self.goal = _text(payload.get("goal"), self.goal)
        self.tasks = tasks
        self.status = PLAN_ACTIVE
        self.current_task_id = ""
        self._normalize()
        return self

    def mark_running(self):
        task = self.current_task() or self.next_task()
        if task is None:
            self.status = PLAN_COMPLETED
            self.touch()
            return None
        self.current_task_id = task.task_id
        if task.status in {TASK_PENDING, TASK_BLOCKED, TASK_FAILED}:
            task.status = TASK_RUNNING
            task.blocker = ""
        task.attempts += 1
        task.touch()
        self.status = PLAN_ACTIVE
        self.touch()
        return task

    def record_tool(self, name):
        task = self.current_task() or self.mark_running()
        if task is not None:
            task.tool_steps += 1
            task.last_action = f"tool:{_text(name)}"
            task.touch()
        self.touch()
        return task

    def record_verification_failure(self, detail):
        task = self.current_task() or self.mark_running()
        if task is not None:
            task.status = TASK_RUNNING
            task.blocker = _text(detail, "verification failed")
            task.last_action = "verification:failed"
            task.touch()
        self.status = PLAN_ACTIVE
        self.touch()
        return task

    def complete_current(self, result=""):
        task = self.current_task()
        if task is not None:
            task.status = TASK_COMPLETED
            task.result = _text(result)
            task.blocker = ""
            task.touch()
        next_task = self.next_task()
        if next_task is None:
            self.status = PLAN_COMPLETED
        else:
            self.current_task_id = next_task.task_id
        self.touch()
        return task

    def block_current(self, blocker):
        task = self.current_task()
        if task is not None:
            task.status = TASK_BLOCKED
            task.blocker = _text(blocker, "blocked")
            task.touch()
        self.status = PLAN_BLOCKED
        self.touch()
        return task

    def fail_current(self, blocker):
        task = self.current_task()
        if task is not None:
            task.status = TASK_FAILED
            task.blocker = _text(blocker, "failed")
            task.touch()
        self.status = PLAN_FAILED
        self.touch()
        return task

    def render(self, limit=1800):
        lines = [f"Plan {self.plan_id} [{self.status}]", f"Goal: {self.goal}"]
        for index, task in enumerate(self.tasks, start=1):
            marker = "*" if task.task_id == self.current_task_id else "-"
            line = f"{marker} {index}. [{task.status}] {task.title}"
            if task.blocker:
                line += f" (blocker: {task.blocker})"
            lines.append(line)
        text = "Execution plan:\n" + "\n".join(lines)
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

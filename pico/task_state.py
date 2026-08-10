"""一次 ask() 运行过程中的状态机快照。

它回答的是：这次用户请求当前进行到哪了、调了多少次工具、最后为什么停下。
这个对象会被不断写入 task_state.json，供运行中观察和运行后复盘。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"

PHASE_CREATED = "created"
PHASE_PLANNING = "planning"
PHASE_EXECUTING = "executing"
PHASE_VERIFYING = "verifying"
PHASE_WAITING_APPROVAL = "waiting_approval"
PHASE_PAUSED = "paused"
PHASE_COMPLETED = "completed"
PHASE_STOPPED = "stopped"
PHASE_FAILED = "failed"
PHASE_BLOCKED = "blocked"

TERMINAL_PHASES = {PHASE_COMPLETED, PHASE_STOPPED, PHASE_FAILED, PHASE_BLOCKED}
ALLOWED_PHASE_TRANSITIONS = {
    PHASE_CREATED: {PHASE_PLANNING, PHASE_EXECUTING, PHASE_PAUSED, *TERMINAL_PHASES},
    PHASE_PLANNING: {PHASE_EXECUTING, PHASE_VERIFYING, PHASE_WAITING_APPROVAL, PHASE_PAUSED, *TERMINAL_PHASES},
    PHASE_EXECUTING: {PHASE_PLANNING, PHASE_VERIFYING, PHASE_WAITING_APPROVAL, PHASE_PAUSED, *TERMINAL_PHASES},
    PHASE_VERIFYING: {PHASE_PLANNING, PHASE_EXECUTING, PHASE_PAUSED, *TERMINAL_PHASES},
    PHASE_WAITING_APPROVAL: {PHASE_PLANNING, PHASE_EXECUTING, PHASE_PAUSED, *TERMINAL_PHASES},
    PHASE_PAUSED: {PHASE_PLANNING, PHASE_EXECUTING, *TERMINAL_PHASES},
    PHASE_COMPLETED: set(),
    PHASE_STOPPED: set(),
    PHASE_FAILED: set(),
}

STOP_REASON_FINAL_ANSWER_RETURNED = "final_answer_returned"
STOP_REASON_STEP_LIMIT_REACHED = "step_limit_reached"
STOP_REASON_RETRY_LIMIT_REACHED = "retry_limit_reached"
STOP_REASON_MODEL_ERROR = "model_error"
STOP_REASON_TOOL_TIMEOUT = "tool_timeout"
STOP_REASON_APPROVAL_DENIED = "approval_denied"
STOP_REASON_DELEGATE_FAILED = "delegate_failed"
STOP_REASON_PERSISTENCE_ERROR = "persistence_error"
STOP_REASON_RESUME_LOAD_ERROR = "resume_load_error"
STOP_REASON_VERIFICATION_FAILED = "verification_failed"
STOP_REASON_BLOCKED = "blocked"


@dataclass
class TaskState:
    run_id: str
    task_id: str
    user_request: str
    status: str = STATUS_RUNNING
    tool_steps: int = 0
    attempts: int = 0
    last_tool: str = ""
    stop_reason: str = ""
    final_answer: str = ""
    checkpoint_id: str = ""
    resume_status: str = ""
    plan_id: str = ""
    current_task_id: str = ""
    verification_attempts: int = 0
    verification_status: str = ""
    verification_error: str = ""
    phase: str = PHASE_CREATED
    phase_history: list = field(default_factory=list)
    edit_required: bool = False
    work_stage: str = ""
    work_stage_history: list = field(default_factory=list)
    discovery_tool_steps: int = 0
    write_tool_steps: int = 0
    verification_tool_steps: int = 0
    first_edit_step: int = 0
    read_only_streak: int = 0
    repeated_tool_rejections: int = 0
    policy_notices: list = field(default_factory=list)
    task_mode: str = "auto"
    stage_attempts: dict = field(default_factory=dict)
    blocked: dict = field(default_factory=dict)
    verification_repair_count: int = 0
    final_verifier_passed: bool = False

    @classmethod
    def create(cls, task_id, user_request, run_id=""):
        if not run_id:
            run_id = "run_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        state = cls(run_id=run_id, task_id=task_id, user_request=user_request)
        state._record_phase(PHASE_CREATED, reason="task_created")
        return state

    @classmethod
    def from_dict(cls, data):
        status = str(data.get("status", STATUS_RUNNING))
        default_phase = {
            STATUS_COMPLETED: PHASE_COMPLETED,
            STATUS_STOPPED: PHASE_STOPPED,
            STATUS_FAILED: PHASE_FAILED,
            STATUS_BLOCKED: PHASE_BLOCKED,
        }.get(status, PHASE_CREATED)
        return cls(
            run_id=str(data.get("run_id", "")),
            task_id=str(data.get("task_id", "")),
            user_request=str(data.get("user_request", "")),
            status=status,
            tool_steps=int(data.get("tool_steps", 0)),
            attempts=int(data.get("attempts", 0)),
            last_tool=str(data.get("last_tool", "")),
            stop_reason=str(data.get("stop_reason", "")),
            final_answer=str(data.get("final_answer", "")),
            checkpoint_id=str(data.get("checkpoint_id", "")),
            resume_status=str(data.get("resume_status", "")),
            plan_id=str(data.get("plan_id", "")),
            current_task_id=str(data.get("current_task_id", "")),
            verification_attempts=int(data.get("verification_attempts", 0)),
            verification_status=str(data.get("verification_status", "")),
            verification_error=str(data.get("verification_error", "")),
            phase=str(data.get("phase", default_phase)),
            phase_history=list(data.get("phase_history", []) or []),
            edit_required=bool(data.get("edit_required", False)),
            work_stage=str(data.get("work_stage", "")),
            work_stage_history=list(data.get("work_stage_history", []) or []),
            discovery_tool_steps=int(data.get("discovery_tool_steps", 0)),
            write_tool_steps=int(data.get("write_tool_steps", 0)),
            verification_tool_steps=int(data.get("verification_tool_steps", 0)),
            first_edit_step=int(data.get("first_edit_step", 0)),
            read_only_streak=int(data.get("read_only_streak", 0)),
            repeated_tool_rejections=int(data.get("repeated_tool_rejections", 0)),
            policy_notices=list(data.get("policy_notices", []) or []),
            task_mode=str(data.get("task_mode", "auto")),
            stage_attempts=dict(data.get("stage_attempts", {}) or {}),
            blocked=dict(data.get("blocked", {}) or {}),
            verification_repair_count=int(data.get("verification_repair_count", 0)),
            final_verifier_passed=bool(data.get("final_verifier_passed", False)),
        )

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat()

    def _record_phase(self, phase, reason=""):
        entry = {"phase": str(phase), "created_at": self._timestamp()}
        if reason:
            entry["reason"] = str(reason)
        self.phase_history.append(entry)

    def transition(self, phase, reason=""):
        """Move to a legal runtime phase without changing completion status."""

        phase = str(phase)
        if phase == self.phase:
            return self
        allowed = ALLOWED_PHASE_TRANSITIONS.get(self.phase, set())
        if phase not in allowed:
            raise ValueError(f"invalid task phase transition: {self.phase} -> {phase}")
        self.phase = phase
        self._record_phase(phase, reason=reason)
        return self

    def transition_work_stage(self, stage, reason=""):
        stage = str(stage or "")
        if not stage or stage == self.work_stage:
            return self
        self.work_stage = stage
        entry = {"stage": stage, "created_at": self._timestamp()}
        if reason:
            entry["reason"] = str(reason)
        self.work_stage_history.append(entry)
        return self

    def record_attempt(self):
        # attempt 统计的是“模型被调用了几轮”，不等于 tool_steps。
        self.attempts += 1
        return self

    def record_tool(self, name):
        # tool_steps 只统计真正进入执行阶段的工具调用次数。
        self.tool_steps += 1
        self.last_tool = str(name or "")
        return self

    def stop(self, stop_reason, status=STATUS_STOPPED, final_answer=""):
        # stop_reason 和 status 分开存，是为了区分“怎么停的”和“停下时是什么状态”。
        self.status = status
        self.stop_reason = stop_reason
        if final_answer != "":
            self.final_answer = final_answer
        terminal_phase = PHASE_FAILED if status == STATUS_FAILED else PHASE_STOPPED
        if self.phase not in TERMINAL_PHASES:
            self.transition(terminal_phase, reason=stop_reason)
        return self

    def stop_step_limit(self, final_answer=""):
        return self.stop(STOP_REASON_STEP_LIMIT_REACHED, final_answer=final_answer)

    def stop_retry_limit(self, final_answer=""):
        return self.stop(STOP_REASON_RETRY_LIMIT_REACHED, final_answer=final_answer)

    def stop_model_error(self, final_answer=""):
        return self.stop(STOP_REASON_MODEL_ERROR, status=STATUS_FAILED, final_answer=final_answer)

    def stop_verification_failed(self, final_answer=""):
        return self.stop(STOP_REASON_VERIFICATION_FAILED, status=STATUS_FAILED, final_answer=final_answer)

    def stop_blocked(self, payload):
        payload = dict(payload or {})
        self.status = STATUS_BLOCKED
        self.stop_reason = STOP_REASON_BLOCKED
        self.blocked = payload
        self.final_answer = str(payload.get("reason", "Task blocked"))
        if self.phase not in TERMINAL_PHASES:
            self.transition(PHASE_BLOCKED, reason=STOP_REASON_BLOCKED)
        return self

    def finish_success(self, final_answer):
        self.status = STATUS_COMPLETED
        self.stop_reason = STOP_REASON_FINAL_ANSWER_RETURNED
        self.final_answer = str(final_answer)
        if self.phase not in TERMINAL_PHASES:
            self.transition(PHASE_COMPLETED, reason=STOP_REASON_FINAL_ANSWER_RETURNED)
        return self

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "user_request": self.user_request,
            "status": self.status,
            "tool_steps": self.tool_steps,
            "attempts": self.attempts,
            "last_tool": self.last_tool,
            "stop_reason": self.stop_reason,
            "final_answer": self.final_answer,
            "checkpoint_id": self.checkpoint_id,
            "resume_status": self.resume_status,
            "plan_id": self.plan_id,
            "current_task_id": self.current_task_id,
            "verification_attempts": self.verification_attempts,
            "verification_status": self.verification_status,
            "verification_error": self.verification_error,
            "phase": self.phase,
            "phase_history": list(self.phase_history),
            "edit_required": self.edit_required,
            "work_stage": self.work_stage,
            "work_stage_history": list(self.work_stage_history),
            "discovery_tool_steps": self.discovery_tool_steps,
            "write_tool_steps": self.write_tool_steps,
            "verification_tool_steps": self.verification_tool_steps,
            "first_edit_step": self.first_edit_step,
            "read_only_streak": self.read_only_streak,
            "repeated_tool_rejections": self.repeated_tool_rejections,
            "policy_notices": list(self.policy_notices),
            "task_mode": self.task_mode,
            "stage_attempts": dict(self.stage_attempts),
            "blocked": dict(self.blocked),
            "verification_repair_count": self.verification_repair_count,
            "final_verifier_passed": self.final_verifier_passed,
        }

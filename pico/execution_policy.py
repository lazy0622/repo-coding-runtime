"""Bounded execution policy for autonomous repository-editing runs.

The lifecycle state in :mod:`pico.task_state` records whether the runtime is
planning, executing, or verifying.  This module tracks a different dimension:
whether the model is still exploring, has enough evidence to diagnose, has
started editing, or is verifying the change.  Keeping these concepts separate
lets the normal tool lifecycle remain stable while the supervisor prevents
read-only research loops.
"""

import re
from dataclasses import dataclass

STAGE_EXPLORE = "explore"
STAGE_DIAGNOSE = "diagnose"
STAGE_EDIT = "edit"
STAGE_VERIFY = "verify"
STAGE_FINISH = "finish"
TASK_MODES = ("auto", "inspect", "edit", "verify")

DISCOVERY_TOOLS = {
    "find_files",
    "search_code",
    "read_file",
    "list_dir",
    "get_file_outline",
    "find_symbol",
    "find_references",
    "repo_index_status",
}
EDIT_TOOLS = {"write_file", "patch_file", "apply_patch", "rollback_patch", "run_coding_workflow"}
VERIFY_TOOLS = {"run_shell"}

_EDIT_INTENT_EN = re.compile(
    r"(?i)\b(fix|implement|add|update|change|modify|remove|delete|rename|refactor|patch|repair|create|write)\b"
)
_EDIT_INTENT_ZH = re.compile(r"(修复|实现|新增|添加|更新|修改|删除|移除|重命名|重构|打补丁|创建|编写|补齐|替换)")
_READ_ONLY_INTENT_EN = re.compile(r"(?i)\b(explain|summarize|inspect|analy[sz]e|review|locate|find where|how does)\b")
_READ_ONLY_INTENT_ZH = re.compile(r"(解释|总结|分析|审查|查看|定位|梳理|怎么实现|如何工作)")
_NEGATED_EDIT_EN = re.compile(
    r"(?i)\b(?:do not|don't|without|never)\s+(?:fix|implement|add|update|change|modify|remove|delete|rename|refactor|patch|repair|create|write)\b"
)
_NEGATED_EDIT_ZH = re.compile(r"(?:不要|无需|不需要|禁止)(?:进行)?(?:修复|实现|新增|添加|更新|修改|删除|移除|重命名|重构|创建|编写)")


@dataclass(frozen=True)
class ExecutionPolicyConfig:
    enabled: bool = True
    explore_budget: int = 6
    diagnose_budget: int = 2
    first_edit_deadline: int = 10
    verification_budget: int = 4
    repair_attempts: int = 2
    read_only_streak_limit: int = 8
    require_edit: bool | None = None

    @property
    def discovery_budget(self):
        return self.explore_budget

    @classmethod
    def from_value(cls, value=None):
        if isinstance(value, cls):
            return value
        value = dict(value or {})
        return cls(
            enabled=bool(value.get("enabled", True)),
            explore_budget=max(1, int(value.get("explore_budget", value.get("discovery_budget", 6)))),
            diagnose_budget=max(1, int(value.get("diagnose_budget", 2))),
            first_edit_deadline=max(2, int(value.get("first_edit_deadline", 10))),
            verification_budget=max(1, int(value.get("verification_budget", 4))),
            repair_attempts=max(0, int(value.get("repair_attempts", 2))),
            read_only_streak_limit=max(2, int(value.get("read_only_streak_limit", 8))),
            require_edit=value.get("require_edit"),
        )


class ExecutionPolicy:
    def __init__(self, config=None):
        self.config = ExecutionPolicyConfig.from_value(config)

    @staticmethod
    def infer_edit_intent(user_request):
        text = str(user_request or "")
        positive_text = _NEGATED_EDIT_ZH.sub("", _NEGATED_EDIT_EN.sub("", text))
        has_edit = bool(_EDIT_INTENT_EN.search(positive_text) or _EDIT_INTENT_ZH.search(positive_text))
        has_read_only = bool(_READ_ONLY_INTENT_EN.search(text) or _READ_ONLY_INTENT_ZH.search(text))
        return has_edit and not (has_read_only and not (_EDIT_INTENT_ZH.search(positive_text)))

    def start(self, task_state, user_request, task_mode="auto"):
        task_mode = str(task_mode or "auto").lower()
        if task_mode not in TASK_MODES:
            raise ValueError(f"unknown task_mode: {task_mode}")
        task_state.task_mode = task_mode
        required = self.config.require_edit
        if required is None:
            task_state.edit_required = task_mode == "edit" or (
                task_mode == "auto" and self.infer_edit_intent(user_request)
            )
        else:
            task_state.edit_required = bool(required)
        initial_stage = STAGE_VERIFY if task_mode == "verify" else STAGE_EXPLORE
        task_state.transition_work_stage(initial_stage, reason=f"task_started:{task_mode}")

    def directive(self, task_state):
        config = self.config
        return (
            f"Execution contract: task_mode={task_state.task_mode}; edit_required={str(task_state.edit_required).lower()}; "
            f"budgets explore={config.explore_budget}, diagnose={config.diagnose_budget}, "
            f"first_edit_deadline={config.first_edit_deadline}, verify={config.verification_budget}, "
            f"repair={config.repair_attempts}. Use <blocked>{{\"reason\":...,\"evidence\":[],"
            "\"required_input\":...}</blocked> when safe completion is impossible."
        )

    @staticmethod
    def record_model_attempt(task_state):
        stage = task_state.work_stage or STAGE_EXPLORE
        task_state.stage_attempts[stage] = int(task_state.stage_attempts.get(stage, 0)) + 1

    def _notice_once(self, task_state, code, message):
        if code in task_state.policy_notices:
            return ""
        task_state.policy_notices.append(code)
        return message

    def before_model(self, task_state):
        if not self.config.enabled or not task_state.edit_required or task_state.write_tool_steps:
            return ""
        if task_state.tool_steps >= self.config.first_edit_deadline - 1:
            task_state.transition_work_stage(STAGE_EDIT, reason="first_edit_deadline")
            return self._notice_once(
                task_state,
                "first_edit_deadline",
                "Supervisor: the first-edit deadline has been reached. Stop repository discovery. "
                "Apply the smallest evidence-backed patch now, or return a focused blocker explaining "
                "exactly what prevents a safe edit.",
            )
        if task_state.work_stage == STAGE_EXPLORE and (
            task_state.discovery_tool_steps >= self.config.explore_budget
            or task_state.read_only_streak >= self.config.read_only_streak_limit
        ):
            task_state.transition_work_stage(STAGE_DIAGNOSE, reason="discovery_budget_exhausted")
            return self._notice_once(
                task_state,
                "diagnosis_required",
                "Supervisor: discovery budget exhausted. State the likely root cause and target files, "
                "then move to a minimal patch. Do not repeat already observed reads.",
            )
        if task_state.work_stage == STAGE_DIAGNOSE and "diagnosis_required" not in task_state.policy_notices:
            return self._notice_once(
                task_state,
                "diagnosis_required",
                "Supervisor: discovery budget exhausted. State the likely root cause and target files, "
                "then move to a minimal patch. Do not repeat already observed reads.",
            )
        if (
            task_state.work_stage == STAGE_DIAGNOSE
            and int(task_state.stage_attempts.get(STAGE_DIAGNOSE, 0)) >= self.config.diagnose_budget
        ):
            task_state.transition_work_stage(STAGE_EDIT, reason="diagnose_budget_exhausted")
            return self._notice_once(
                task_state,
                "diagnose_budget_exhausted",
                "Supervisor: diagnosis budget exhausted. Apply the smallest supported patch now or return <blocked>.",
            )
        return ""

    def assess_tool(self, task_state, name):
        """Return ``(allowed, reason)`` before a tool reaches the gateway."""

        if not self.config.enabled or not task_state.edit_required:
            return True, ""
        if (
            name in DISCOVERY_TOOLS
            and not task_state.write_tool_steps
            and task_state.tool_steps >= self.config.first_edit_deadline
        ):
            return False, "first_edit_deadline_exceeded"
        if name in VERIFY_TOOLS and task_state.verification_tool_steps >= self.config.verification_budget:
            return False, "verification_budget_exceeded"
        return True, ""

    def record_tool(self, task_state, name, metadata):
        metadata = dict(metadata or {})
        changed = bool(metadata.get("workspace_changed"))
        if name in EDIT_TOOLS and changed:
            task_state.write_tool_steps += 1
            task_state.read_only_streak = 0
            if not task_state.first_edit_step:
                task_state.first_edit_step = task_state.tool_steps
            task_state.transition_work_stage(STAGE_EDIT, reason=f"workspace_changed:{name}")
            return

        if name in VERIFY_TOOLS and task_state.write_tool_steps:
            if metadata.get("tool_error_code") == "verification_budget_exceeded":
                return
            task_state.verification_tool_steps += 1
            task_state.read_only_streak = 0
            task_state.transition_work_stage(STAGE_VERIFY, reason=f"verification_tool:{name}")
            return

        if name in DISCOVERY_TOOLS:
            task_state.discovery_tool_steps += 1
            task_state.read_only_streak += 1
            if task_state.discovery_tool_steps >= self.config.explore_budget:
                task_state.transition_work_stage(STAGE_DIAGNOSE, reason="discovery_budget_exhausted")
        elif metadata.get("read_only", True):
            task_state.read_only_streak += 1
        else:
            task_state.read_only_streak = 0

        if metadata.get("tool_error_code") == "repeated_identical_call":
            task_state.repeated_tool_rejections += 1

    def final_allowed(self, task_state):
        if not self.config.enabled or not task_state.edit_required:
            return True, ""
        if task_state.write_tool_steps:
            task_state.transition_work_stage(STAGE_FINISH, reason="final_after_edit")
            return True, ""
        return False, "edit_required_but_no_workspace_change"

    def start_verification(self, task_state, reason="verification_started"):
        if self.config.enabled and task_state.write_tool_steps:
            task_state.transition_work_stage(STAGE_VERIFY, reason=reason)

    def record_runtime_verification(self, task_state, passed):
        task_state.final_verifier_passed = bool(passed)
        if not passed:
            task_state.verification_repair_count += 1

    def can_repair_verification(self, task_state):
        return task_state.verification_repair_count <= self.config.repair_attempts

    def summary(self, task_state):
        return {
            "enabled": self.config.enabled,
            "edit_required": task_state.edit_required,
            "work_stage": task_state.work_stage,
            "discovery_budget": self.config.discovery_budget,
            "explore_budget": self.config.explore_budget,
            "diagnose_budget": self.config.diagnose_budget,
            "first_edit_deadline": self.config.first_edit_deadline,
            "verification_budget": self.config.verification_budget,
            "repair_attempt_budget": self.config.repair_attempts,
            "discovery_tool_steps": task_state.discovery_tool_steps,
            "write_tool_steps": task_state.write_tool_steps,
            "verification_tool_steps": task_state.verification_tool_steps,
            "agent_verification_steps": task_state.verification_tool_steps,
            "runtime_verification_attempts": task_state.verification_attempts,
            "verification_repair_count": task_state.verification_repair_count,
            "final_verifier_passed": task_state.final_verifier_passed,
            "first_edit_step": task_state.first_edit_step,
            "read_only_streak": task_state.read_only_streak,
            "repeated_tool_rejections": task_state.repeated_tool_rejections,
            "policy_notices": list(task_state.policy_notices),
            "stage_history": list(task_state.work_stage_history),
            "stage_attempts": dict(task_state.stage_attempts),
            "task_mode": task_state.task_mode,
        }

"""Agent control loop extracted from the runtime facade."""

import time
import json
import re

from .checkpoint import CHECKPOINT_NONE_STATUS, CHECKPOINT_PARTIAL_STALE_STATUS, CHECKPOINT_WORKSPACE_MISMATCH_STATUS
from .task_state import TaskState
from .task_state import PHASE_EXECUTING, PHASE_PLANNING, PHASE_VERIFYING
from .verification import VERIFY_BLOCKED
from .workspace import clip, now


class AgentLoop:
    def __init__(self, agent):
        self.agent = agent

    @staticmethod
    def _transition(agent, task_state, phase, reason):
        previous = task_state.phase
        task_state.transition(phase, reason=reason)
        if previous == task_state.phase:
            return
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "state_changed",
            {
                "previous_phase": previous,
                "current_phase": task_state.phase,
                "reason": reason,
            },
        )

    @staticmethod
    def _emit_terminal_transition(agent, task_state, previous_phase, reason):
        agent.run_store.write_task_state(task_state)
        agent.emit_trace(
            task_state,
            "state_changed",
            {
                "previous_phase": previous_phase,
                "current_phase": task_state.phase,
                "reason": reason,
            },
        )

    @staticmethod
    def _sync_plan_progress(agent, task_state):
        plan = agent.current_plan()
        if plan is not None:
            task_state.plan_id = plan.plan_id
            task_state.current_task_id = plan.current_task_id
        return plan

    @staticmethod
    def _is_resume_request(agent, user_message):
        if not agent.current_plan() or agent.resume_state.get("status") == CHECKPOINT_NONE_STATUS:
            return False
        return bool(re.search(r"(?i)\b(resume|continue|继续|恢复|接着)\b", str(user_message or "")))

    def run(self, user_message):
        agent = self.agent
        run_started_at = time.monotonic()
        agent.memory.set_task_summary(user_message)
        agent.record({"role": "user", "content": user_message, "created_at": now()})

        task_state = TaskState.create(run_id=agent.new_run_id(), task_id=agent.new_task_id(), user_request=user_message)
        agent.execution_policy.start(task_state, user_message, task_mode=agent.task_mode)
        agent.record(
            {
                "role": "supervisor",
                "content": agent.execution_policy.directive(task_state),
                "created_at": now(),
            }
        )
        task_state.resume_status = agent.resume_state.get("status", CHECKPOINT_NONE_STATUS)
        plan = agent.start_plan(user_message, preserve_existing=self._is_resume_request(agent, user_message))
        task_state.plan_id = plan.plan_id
        task_state.current_task_id = plan.current_task_id
        agent.last_verification = {}
        agent.current_task_state = task_state
        agent.current_run_dir = agent.run_store.start_run(task_state)
        agent.emit_trace(
            task_state,
            "run_started",
            {
                "task_id": task_state.task_id,
                "user_request": clip(user_message, 300),
            },
        )
        self._transition(agent, task_state, PHASE_PLANNING, "run_started")

        tool_steps = 0
        attempts = 0
        max_attempts = max(agent.max_steps * 3, agent.max_steps + 4)

        # 这是 agent 的主循环，可以按“感知 -> 决策 -> 行动 -> 记录”来理解：
        # 1. 感知：重新组 prompt，把当前状态整理给模型看
        # 2. 决策：让模型返回一个工具调用，或一个最终答案
        # 3. 行动：如果是工具调用，就执行工具
        # 4. 记录：把结果写回 history / task_state / trace / memory
        # 然后进入下一轮，直到停机条件满足
        while tool_steps < agent.max_steps and attempts < max_attempts:
            attempts += 1
            task_state.record_attempt()
            agent.execution_policy.record_model_attempt(task_state)
            supervisor_notice = agent.execution_policy.before_model(task_state)
            if supervisor_notice:
                agent.record({"role": "supervisor", "content": supervisor_notice, "created_at": now()})
                agent.emit_trace(
                    task_state,
                    "execution_policy_notice",
                    {"stage": task_state.work_stage, "notice": supervisor_notice},
                )
            agent.run_store.write_task_state(task_state)
            prompt_started_at = time.monotonic()
            prompt, prompt_metadata = agent._build_prompt_and_metadata(user_message)
            agent.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                },
            )
            if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="freshness_mismatch")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "freshness_mismatch",
                    },
                )
            elif prompt_metadata.get("resume_status") == CHECKPOINT_WORKSPACE_MISMATCH_STATUS:
                agent.emit_trace(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(prompt_metadata.get("runtime_identity_mismatch_fields", [])),
                    },
                )
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="workspace_mismatch")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "workspace_mismatch",
                    },
                )
            if prompt_metadata.get("budget_reductions"):
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="context_reduction")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "context_reduction",
                    },
                )
            agent.emit_trace(
                task_state,
                "model_requested",
                {
                    "attempts": task_state.attempts,
                    "tool_steps": task_state.tool_steps,
                    "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
                },
            )
            prompt_cache_key = None
            prompt_cache_retention = None
            if getattr(agent.model_client, "supports_prompt_cache", False):
                # 只有后端明确支持时，才把稳定前缀的 hash 作为 cache key 发出去。
                prompt_cache_key = prompt_metadata.get("prompt_cache_key")
                prompt_cache_retention = "in_memory"
            model_started_at = time.monotonic()
            try:
                raw = agent.model_client.complete(
                    prompt,
                    agent.max_new_tokens,
                    prompt_cache_key=prompt_cache_key,
                    prompt_cache_retention=prompt_cache_retention,
                )
            except Exception as exc:
                previous_phase = task_state.phase
                agent.fail_plan(str(exc))
                self._sync_plan_progress(agent, task_state)
                task_state.stop_model_error(str(exc))
                self._emit_terminal_transition(agent, task_state, previous_phase, "model_error")
                agent.emit_trace(
                    task_state,
                    "model_failed",
                    {
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                        "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                    },
                )
                agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
                raise
            completion_metadata = dict(getattr(agent.model_client, "last_completion_metadata", {}) or {})
            if completion_metadata:
                # 把后端返回的 usage/cache 统计并回 prompt_metadata，
                # 方便统一写入 report 和 trace。
                prompt_metadata.update(completion_metadata)
            agent.last_completion_metadata = completion_metadata
            agent.last_prompt_metadata = prompt_metadata
            kind, payload = agent.parse(raw)
            agent.emit_trace(
                task_state,
                "model_parsed",
                {
                    "kind": kind,
                    "completion_metadata": completion_metadata,
                    "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                },
            )

            if kind == "plan":
                if not agent.plan_mode:
                    agent.record({"role": "assistant", "content": agent.retry_notice("planning is disabled"), "created_at": now()})
                    agent.run_store.write_task_state(task_state)
                    continue
                try:
                    plan = agent.apply_model_plan(payload)
                except ValueError as exc:
                    notice = agent.retry_notice(str(exc))
                    agent.record({"role": "assistant", "content": notice, "created_at": now()})
                    agent.run_store.write_task_state(task_state)
                    continue
                task_state.plan_id = plan.plan_id
                task_state.current_task_id = plan.current_task_id
                agent.record({"role": "assistant", "content": "<plan>" + json.dumps(payload, ensure_ascii=False) + "</plan>", "created_at": now()})
                agent.emit_trace(task_state, "plan_updated", {"plan": plan.to_dict(), "source": "model"})
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="plan_updated")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {"checkpoint_id": checkpoint["checkpoint_id"], "trigger": "plan_updated"},
                )
                continue

            if kind == "tool":
                tool_steps += 1
                name = payload.get("name", "")
                args = payload.get("args", {})
                task_state.record_tool(name)
                agent.record_plan_tool(name)
                self._sync_plan_progress(agent, task_state)
                self._transition(agent, task_state, PHASE_EXECUTING, f"tool:{name}")
                tool_result = agent.execute_tool(name, args)
                agent.execution_policy.record_tool(task_state, name, tool_result.metadata)
                result = tool_result.content
                agent.record(
                    {
                        "role": "tool",
                        "name": name,
                        "args": args,
                        "content": result,
                        "created_at": now(),
                    }
                )
                agent.run_store.write_task_state(task_state)
                # Read-only tools are already represented in session history;
                # create a durable checkpoint only after risky or workspace-
                # changing work, rather than after every harmless read.
                metadata = dict(tool_result.metadata or {})
                if not bool(metadata.get("read_only", True)) or bool(metadata.get("workspace_changed")):
                    checkpoint = agent.create_checkpoint(task_state, user_message, trigger="tool_finished")
                    agent.run_store.write_task_state(task_state)
                    agent.emit_trace(
                        task_state,
                        "checkpoint_created",
                        {
                            "checkpoint_id": checkpoint["checkpoint_id"],
                            "trigger": "tool_finished",
                        },
                    )
                self._transition(agent, task_state, PHASE_PLANNING, "tool_finished")
                continue

            if kind == "retry":
                agent.record({"role": "assistant", "content": payload, "created_at": now()})
                agent.run_store.write_task_state(task_state)
                continue

            if kind == "blocked":
                agent.record(
                    {
                        "role": "assistant",
                        "content": "<blocked>" + json.dumps(payload, ensure_ascii=False) + "</blocked>",
                        "created_at": now(),
                    }
                )
                agent.block_plan(payload["reason"])
                self._sync_plan_progress(agent, task_state)
                previous_phase = task_state.phase
                task_state.stop_blocked(payload)
                self._emit_terminal_transition(agent, task_state, previous_phase, "blocked")
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="blocked")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {"checkpoint_id": checkpoint["checkpoint_id"], "trigger": "blocked"},
                )
                agent.emit_trace(
                    task_state,
                    "run_finished",
                    {
                        "status": task_state.status,
                        "stop_reason": task_state.stop_reason,
                        "blocked": payload,
                        "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
                    },
                )
                agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
                return f"Blocked: {payload['reason']}"

            final = (payload or raw).strip()
            final_allowed, final_reason = agent.execution_policy.final_allowed(task_state)
            if not final_allowed:
                notice = agent.retry_notice(
                    "this request requires a repository edit, but no workspace-changing tool has succeeded; "
                    "apply a minimal patch or return a concrete blocker"
                )
                agent.record({"role": "assistant", "content": final, "created_at": now()})
                agent.record({"role": "supervisor", "content": notice, "created_at": now()})
                agent.emit_trace(
                    task_state,
                    "premature_final_rejected",
                    {"reason": final_reason, "stage": task_state.work_stage},
                )
                agent.run_store.write_task_state(task_state)
                continue
            agent.record({"role": "assistant", "content": final, "created_at": now()})

            if agent.verify_command:
                previous_phase = task_state.phase
                agent.execution_policy.start_verification(task_state)
                self._transition(agent, task_state, PHASE_VERIFYING, "verification_started")
                agent.emit_trace(
                    task_state,
                    "verification_started",
                    {"command": agent.verify_command, "attempt": task_state.verification_attempts + 1},
                )
                verification_result = agent.run_verification(task_state)
                agent.execution_policy.record_runtime_verification(task_state, verification_result.passed)
                verification_payload = agent.redact_artifact(verification_result.to_dict())
                agent.emit_trace(task_state, "verification_finished", verification_payload)
                if not verification_result.passed:
                    detail = clip(
                        verification_result.stderr or verification_result.stdout or verification_result.reason or verification_result.error_code,
                        1200,
                    )
                    if (
                        verification_result.status != VERIFY_BLOCKED
                        and task_state.verification_attempts <= agent.max_verification_attempts
                        and agent.execution_policy.can_repair_verification(task_state)
                    ):
                        plan = agent.current_plan()
                        if plan is not None:
                            plan.record_verification_failure(detail)
                            agent.session["plan"] = plan.to_dict()
                            agent.session_path = agent.session_store.save(agent.session)
                        self._sync_plan_progress(agent, task_state)
                        agent.record(
                            {
                                "role": "verification",
                                "content": f"Verification failed (attempt {task_state.verification_attempts}): {detail}",
                                "created_at": now(),
                            }
                        )
                        agent.emit_trace(
                            task_state,
                            "verification_retry",
                            {"attempt": task_state.verification_attempts, "reason": detail},
                        )
                        agent.run_store.write_task_state(task_state)
                        self._transition(agent, task_state, PHASE_PLANNING, "verification_failed_retry")
                        continue

                    final = f"Verification failed after {task_state.verification_attempts} attempt(s): {detail}"
                    agent.fail_plan(detail)
                    self._sync_plan_progress(agent, task_state)
                    terminal_previous_phase = task_state.phase
                    task_state.stop_verification_failed(final)
                    self._emit_terminal_transition(agent, task_state, terminal_previous_phase, "verification_failed")
                    checkpoint = agent.create_checkpoint(task_state, user_message, trigger="verification_failed")
                    agent.run_store.write_task_state(task_state)
                    agent.emit_trace(
                        task_state,
                        "checkpoint_created",
                        {"checkpoint_id": checkpoint["checkpoint_id"], "trigger": "verification_failed"},
                    )
                    agent.emit_trace(
                        task_state,
                        "run_finished",
                        {
                            "status": task_state.status,
                            "stop_reason": task_state.stop_reason,
                            "final_answer": final,
                            "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
                        },
                    )
                    agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
                    return final

            completed_task_id = task_state.current_task_id
            completed_plan = agent.complete_plan(final)
            self._sync_plan_progress(agent, task_state)
            if completed_plan is not None and completed_plan.status != "completed":
                agent.emit_trace(
                    task_state,
                    "plan_task_completed",
                    {
                        "completed_task_id": completed_task_id,
                        "plan": completed_plan.to_dict(),
                    },
                )
                agent.run_store.write_task_state(task_state)
                self._transition(agent, task_state, PHASE_PLANNING, "plan_task_completed")
                continue
            previous_phase = task_state.phase
            task_state.finish_success(final)
            self._emit_terminal_transition(
                agent,
                task_state,
                previous_phase,
                "final_answer_returned",
            )
            agent.promote_durable_memory(user_message, final)
            checkpoint = agent.create_checkpoint(task_state, user_message, trigger="run_finished")
            agent.run_store.write_task_state(task_state)
            agent.emit_trace(
                task_state,
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "trigger": "run_finished",
                },
            )
            agent.emit_trace(
                task_state,
                "run_finished",
                {
                    "status": task_state.status,
                    "stop_reason": task_state.stop_reason,
                    "final_answer": final,
                    "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
                },
            )
            agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
            return final

        if attempts >= max_attempts and tool_steps < agent.max_steps:
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
            previous_phase = task_state.phase
            agent.block_plan("retry_limit_reached")
            task_state.stop_retry_limit(final)
        else:
            final = "Stopped after reaching the step limit without a final answer."
            previous_phase = task_state.phase
            agent.block_plan("step_limit_reached")
            task_state.stop_step_limit(final)
        self._sync_plan_progress(agent, task_state)
        self._emit_terminal_transition(
            agent,
            task_state,
            previous_phase,
            task_state.stop_reason or "run_stopped",
        )
        agent.record({"role": "assistant", "content": final, "created_at": now()})
        agent.promote_durable_memory(user_message, final)
        agent.run_store.write_task_state(task_state)
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger=task_state.stop_reason or "run_stopped")
        agent.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": task_state.stop_reason or "run_stopped",
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
        return final

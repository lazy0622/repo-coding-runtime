"""Structured tool execution for the agent runtime."""

from dataclasses import dataclass
import json
import re
import time

from .task_state import PHASE_EXECUTING, PHASE_WAITING_APPROVAL
from .workspace import clip


@dataclass(frozen=True)
class ToolExecutionResult:
    content: str
    metadata: dict


def _metadata(
    tool_status,
    tool_error_code="",
    security_event_type="",
    risk_level="low",
    read_only=True,
    affected_paths=None,
    workspace_changed=False,
    workspace_fingerprint="",
    diff_summary=None,
):
    result = {
        "tool_status": tool_status,
        "tool_error_code": tool_error_code,
        "security_event_type": security_event_type,
        "risk_level": risk_level,
        "read_only": read_only,
        "affected_paths": list(affected_paths or []),
        "workspace_changed": bool(workspace_changed),
        "diff_summary": list(diff_summary or []),
    }
    if workspace_fingerprint:
        result["workspace_fingerprint"] = workspace_fingerprint
    return result


class ToolGateway:
    def __init__(self, agent):
        self.agent = agent

    def _emit(self, event, payload):
        task_state = getattr(self.agent, "current_task_state", None)
        if task_state is not None:
            self.agent.emit_trace(task_state, event, payload)

    def _transition(self, phase, reason):
        task_state = getattr(self.agent, "current_task_state", None)
        if task_state is None or task_state.phase == phase:
            return
        previous = task_state.phase
        task_state.transition(phase, reason=reason)
        self.agent.run_store.write_task_state(task_state)
        self._emit(
            "state_changed",
            {"previous_phase": previous, "current_phase": phase, "reason": reason},
        )

    def _policy(self, name, decision, reason, tool=None):
        self._emit(
            "policy_decided",
            {
                "name": name,
                "decision": decision,
                "reason": reason,
                "risk_level": "high" if tool and tool.get("risky") else "low",
            },
        )

    def _finish(self, name, args, result):
        # Keep one terminal lifecycle event per call.  The outcome is carried
        # in metadata so success, rejection, failure, and partial success all
        # have the same trace shape.
        self._emit(
            "tool_finished",
            {
                "name": name,
                "args": args,
                "result": result.content,
                **dict(result.metadata),
            },
        )
        return result

    def execute(self, name, args):
        agent = self.agent
        args = args or {}
        self._emit("tool_requested", {"name": name, "args": args})
        if agent.allowed_tools is not None and name not in agent.allowed_tools:
            self._policy(name, "deny", "tool_not_allowed")
            return self._finish(name, args, ToolExecutionResult(
                content=f"error: tool '{name}' is not allowed in this run",
                metadata=_metadata(
                    "rejected",
                    tool_error_code="tool_not_allowed",
                    risk_level="high",
                    read_only=False,
                ),
            ))

        tool = agent.tools.get(name)
        if tool is None:
            self._policy(name, "deny", "unknown_tool")
            return self._finish(name, args, ToolExecutionResult(
                content=f"error: unknown tool '{name}'",
                metadata=_metadata(
                    "rejected",
                    tool_error_code="unknown_tool",
                    risk_level="high",
                    read_only=False,
                ),
            ))

        try:
            agent.validate_tool(name, args)
        except Exception as exc:
            self._policy(name, "deny", "invalid_arguments", tool=tool)
            example = agent.tool_example(name)
            message = f"error: invalid arguments for {name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            security_event_type = "path_escape" if "path escapes workspace" in str(exc) else ""
            return self._finish(name, args, ToolExecutionResult(
                content=message,
                metadata=_metadata(
                    "rejected",
                    tool_error_code="invalid_arguments",
                    security_event_type=security_event_type,
                    risk_level="high" if tool["risky"] else "low",
                    read_only=not tool["risky"],
                ),
            ))

        if agent.repeated_tool_call(name, args):
            self._policy(name, "deny", "repeated_identical_call", tool=tool)
            return self._finish(name, args, ToolExecutionResult(
                content=f"error: repeated identical tool call for {name}; choose a different tool or return a final answer",
                metadata=_metadata(
                    "rejected",
                    tool_error_code="repeated_identical_call",
                    risk_level="high" if tool["risky"] else "low",
                    read_only=not tool["risky"],
                ),
            ))

        command_policy = agent.classify_tool_call(name, args) if hasattr(agent, "classify_tool_call") else {
            "decision": "allow",
            "risk_level": "high" if tool["risky"] else "low",
            "reason": "legacy_policy",
        }
        if command_policy.get("decision") == "deny":
            self._policy(name, "deny", "command_policy_blocked", tool=tool)
            return self._finish(name, args, ToolExecutionResult(
                content=f"error: command blocked by safety policy ({command_policy.get('reason', 'blocked')})",
                metadata=_metadata(
                    "rejected",
                    tool_error_code="command_blocked",
                    security_event_type="command_blocked",
                    risk_level=command_policy.get("risk_level", "critical"),
                    read_only=False,
                ),
            ))

        if tool["risky"] and agent.approval_policy == "ask" and not agent.read_only:
            self._transition(PHASE_WAITING_APPROVAL, f"approval:{name}")
        if tool["risky"] and not agent.approve(name, args):
            self._policy(name, "deny", "approval_denied", tool=tool)
            return self._finish(name, args, ToolExecutionResult(
                content=f"error: approval denied for {name}",
                metadata=_metadata(
                    "rejected",
                    tool_error_code="approval_denied",
                    security_event_type="read_only_block" if agent.read_only else "approval_denied",
                    risk_level="high",
                    read_only=False,
                ),
            ))

        if tool["risky"] and agent.approval_policy == "ask" and not agent.read_only:
            self._transition(PHASE_EXECUTING, f"approval_granted:{name}")
        self._policy(name, "allow", "policy_passed", tool=tool)
        self._emit(
            "tool_started",
            {
                "name": name,
                "args": args,
                "command_risk_level": command_policy.get("risk_level", "high" if tool["risky"] else "low"),
                "command_policy_reason": command_policy.get("reason", ""),
            },
        )
        started_at = time.monotonic()

        before_snapshot = agent.capture_workspace_snapshot() if tool["risky"] else {}
        after_snapshot = before_snapshot
        try:
            content = clip(tool["run"](args))
            after_snapshot = agent.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            affected_paths, diff_summary = agent.diff_workspace_snapshots(before_snapshot, after_snapshot)
            workspace_changed = bool(affected_paths)
            tool_status = "ok"
            tool_error_code = ""
            if name == "run_shell":
                match = re.search(r"exit_code:\s*(-?\d+)", content)
                exit_code = int(match.group(1)) if match else 0
                if exit_code != 0 and workspace_changed:
                    tool_status = "partial_success"
                    tool_error_code = "tool_partial_success"
                elif exit_code != 0:
                    tool_status = "error"
                    tool_error_code = "tool_failed"
            if name == "run_task_graph":
                try:
                    graph_result = json.loads(content)
                except (TypeError, ValueError):
                    graph_result = {}
                if graph_result.get("status") == "failed":
                    tool_status = "error"
                    tool_error_code = "subagent_graph_failed"
                elif any(item.get("status") == "blocked" for item in graph_result.get("tasks", [])):
                    tool_status = "partial_success"
                    tool_error_code = "subagent_graph_partial"
            if name == "run_coding_workflow":
                try:
                    workflow_result = json.loads(content)
                except (TypeError, ValueError):
                    workflow_result = {}
                if workflow_result.get("status") == "failed":
                    tool_status = "error"
                    tool_error_code = "coding_workflow_failed"
                elif workflow_result.get("status") == "rolled_back":
                    tool_status = "partial_success"
                    tool_error_code = "coding_workflow_rolled_back"
            agent.update_memory_after_tool(name, args, content)
            metadata = _metadata(
                tool_status,
                tool_error_code=tool_error_code,
                risk_level="high" if tool["risky"] else "low",
                read_only=not tool["risky"],
                affected_paths=affected_paths,
                workspace_changed=workspace_changed,
                workspace_fingerprint=agent.workspace.fingerprint(),
                diff_summary=diff_summary,
            )
            metadata["duration_ms"] = int((time.monotonic() - started_at) * 1000)
            agent.record_process_note_for_tool(name, metadata)
            return self._finish(name, args, ToolExecutionResult(content=content, metadata=metadata))
        except Exception as exc:
            after_snapshot = agent.capture_workspace_snapshot() if tool["risky"] else before_snapshot
            affected_paths, diff_summary = agent.diff_workspace_snapshots(before_snapshot, after_snapshot)
            workspace_changed = bool(affected_paths)
            security_event_type = "path_escape" if "path escapes workspace" in str(exc) else ""
            metadata = _metadata(
                "partial_success" if workspace_changed else "error",
                tool_error_code="tool_partial_success" if workspace_changed else "tool_failed",
                security_event_type=security_event_type,
                risk_level="high" if tool["risky"] else "low",
                read_only=not tool["risky"],
                affected_paths=affected_paths,
                workspace_changed=workspace_changed,
                workspace_fingerprint=agent.workspace.fingerprint(),
                diff_summary=diff_summary,
            )
            metadata["duration_ms"] = int((time.monotonic() - started_at) * 1000)
            agent.record_process_note_for_tool(name, metadata)
            return self._finish(
                name,
                args,
                ToolExecutionResult(content=f"error: tool {name} failed: {exc}", metadata=metadata),
            )


class ToolExecutor(ToolGateway):
    """Backward-compatible name for the V1 Tool Gateway."""

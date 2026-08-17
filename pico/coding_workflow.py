"""End-to-end V2.4 research, edit, verify, and rollback workflow."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .atomic_io import replace_with_retry
from .patching import PatchError, apply_unified_diff, parse_unified_diff
from .subagents import SubAgentManager
from .verification import run_verification
from .workspace import clip


CODING_WORKFLOW_SCHEMA_VERSION = "coding-workflow-v2.4"


class CodingWorkflowError(ValueError):
    """Raised when the end-to-end coding workflow cannot start safely."""


def _atomic_json(path, payload):
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


def _bounded_json(payload, limit=3600):
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if len(rendered) <= limit:
        return rendered
    compact = {
        "schema_version": payload.get("schema_version", CODING_WORKFLOW_SCHEMA_VERSION),
        "workflow_id": payload.get("workflow_id", ""),
        "status": payload.get("status", "failed"),
        "phase": payload.get("phase", ""),
        "goal": payload.get("goal", ""),
        "research_status": (payload.get("research", {}) or {}).get("status", ""),
        "research_confidence": (payload.get("research", {}) or {}).get("confidence", 0.0),
        "research_evidence_count": len((payload.get("research", {}) or {}).get("evidence", [])),
        "research_summary": clip(payload.get("research_summary", ""), 700),
        "patch": payload.get("patch", {}),
        "verification": payload.get("verification", {}),
        "rollback": payload.get("rollback", {}),
        "artifact_dir": payload.get("artifact_dir", ""),
        "truncated": True,
        "message": "Full workflow evidence is stored in the artifact directory.",
    }
    return json.dumps(compact, ensure_ascii=False, indent=2, sort_keys=True)


class CodingWorkflowManager:
    """Coordinate read-only research with one guarded source edit and verifier."""

    def __init__(self, agent):
        self.agent = agent

    def _artifact_root(self, workflow_id):
        current_run_dir = getattr(self.agent, "current_run_dir", None)
        if current_run_dir:
            root = Path(current_run_dir) / "coding_workflow" / workflow_id
        else:
            session_id = str(self.agent.session.get("id", "session"))
            root = Path(self.agent.root) / ".pico" / "workflows" / session_id / workflow_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _emit(self, event, payload):
        task_state = getattr(self.agent, "current_task_state", None)
        if task_state is not None:
            self.agent.emit_trace(task_state, event, payload)

    @staticmethod
    def _preview(patch_text):
        parsed = parse_unified_diff(patch_text)
        return {
            "files": [
                {
                    "path": item.path,
                    "hunks": len(item.hunks),
                    "old_lines": sum(item.old_count for item in item.hunks),
                    "new_lines": sum(item.new_count for item in item.hunks),
                }
                for item in parsed
            ],
            "patch_chars": len(str(patch_text)),
            "patch_preview": clip(patch_text, 2400),
        }

    def execute(self, args):
        args = dict(args or {})
        goal = str(args.get("goal", "")).strip()
        research_tasks = args.get("research_tasks", args.get("tasks", []))
        patch_text = str(args.get("patch", ""))
        verify_command = str(args.get("verify_command", "")).strip()
        if not goal:
            raise CodingWorkflowError("goal must not be empty")
        if not isinstance(research_tasks, list) or not research_tasks:
            raise CodingWorkflowError("research_tasks must be a non-empty list")
        if not patch_text.strip():
            raise CodingWorkflowError("patch must not be empty")
        if not verify_command:
            raise CodingWorkflowError("verify_command must be explicit for coding workflow")

        workflow_id = str(args.get("workflow_id", "")).strip() or "workflow_" + uuid4().hex[:10]
        artifact_root = self._artifact_root(workflow_id)
        state_path = artifact_root / "workflow.json"
        workflow = {
            "schema_version": CODING_WORKFLOW_SCHEMA_VERSION,
            "workflow_id": workflow_id,
            "goal": goal,
            "phase": "researching",
            "status": "running",
            "rollback_on_failure": bool(args.get("rollback_on_failure", True)),
            "artifact_dir": str(artifact_root),
            "research": {},
            "patch": {},
            "verification": {},
            "rollback": {},
        }
        _atomic_json(state_path, workflow)
        self._emit("coding_workflow_started", {"workflow_id": workflow_id, "goal": goal})

        research_args = {
            "graph_id": args.get("graph_id") or f"{workflow_id}_research",
            "goal": goal,
            "tasks": research_tasks,
            "max_steps": int(args.get("max_steps", 4)),
            "max_task_attempts": int(args.get("max_task_attempts", 1)),
            "task_timeout_seconds": int(args.get("task_timeout_seconds", 120)),
            "isolate_worktrees": bool(args.get("isolate_worktrees", False)),
            "resume": bool(args.get("resume", False)),
        }
        try:
            research = json.loads(SubAgentManager(self.agent).execute(research_args))
        except Exception as exc:
            workflow.update(
                {
                    "status": "failed",
                    "phase": "research_failed",
                    "error": f"research failed: {exc}",
                }
            )
            _atomic_json(state_path, workflow)
            self._emit("coding_workflow_finished", {"workflow_id": workflow_id, "status": "failed", "phase": workflow["phase"]})
            return _bounded_json(workflow)

        workflow["research"] = research
        workflow["research_summary"] = research.get("summary", "")
        self._emit(
            "research_completed",
            {
                "workflow_id": workflow_id,
                "status": research.get("status", ""),
                "summary": clip(research.get("summary", ""), 900),
                "evidence_count": len(research.get("evidence", [])),
            },
        )
        if research.get("status") != "completed":
            workflow.update({"status": "failed", "phase": "research_failed", "error": "research graph did not complete"})
            _atomic_json(state_path, workflow)
            self._emit("coding_workflow_finished", {"workflow_id": workflow_id, "status": "failed", "phase": workflow["phase"]})
            return _bounded_json(workflow)

        workflow["phase"] = "patch_previewed"
        try:
            preview = self._preview(patch_text)
            workflow["patch"] = {"status": "previewed", **preview}
        except (PatchError, ValueError) as exc:
            workflow.update({"status": "failed", "phase": "patch_preview_failed", "error": str(exc)})
            _atomic_json(state_path, workflow)
            self._emit("coding_workflow_finished", {"workflow_id": workflow_id, "status": "failed", "phase": workflow["phase"]})
            return _bounded_json(workflow)
        _atomic_json(state_path, workflow)
        self._emit("patch_previewed", {"workflow_id": workflow_id, **workflow["patch"]})

        try:
            applied = apply_unified_diff(self.agent.root, patch_text, journal=self.agent.patch_journal)
            workflow["patch"].update(applied)
            workflow["phase"] = "patch_applied"
            self._emit("patch_applied", {"workflow_id": workflow_id, **applied})
        except Exception as exc:
            workflow.update({"status": "failed", "phase": "patch_apply_failed", "error": str(exc)})
            _atomic_json(state_path, workflow)
            self._emit("coding_workflow_finished", {"workflow_id": workflow_id, "status": "failed", "phase": workflow["phase"]})
            return _bounded_json(workflow)

        _atomic_json(state_path, workflow)
        self._emit(
            "verification_started",
            {"workflow_id": workflow_id, "command": verify_command},
        )
        verification = run_verification(
            self.agent.root,
            verify_command,
            timeout=int(args.get("verify_timeout", 60)),
            env=self.agent.shell_env(),
            execution_backend=self.agent.execution_backend,
        )
        workflow["verification"] = verification.to_dict()
        self._emit("verification_finished", {"workflow_id": workflow_id, **verification.to_dict()})

        if verification.passed:
            workflow.update({"status": "completed", "phase": "completed"})
        else:
            backup_id = str(workflow["patch"].get("backup_id", ""))
            rollback = {"status": "skipped", "reason": "rollback_on_failure_disabled"}
            if workflow["rollback_on_failure"] and backup_id:
                self._emit("rollback_started", {"workflow_id": workflow_id, "backup_id": backup_id})
                try:
                    rollback = self.agent.patch_journal.rollback(backup_id)
                    rollback["reason"] = "verification_failed"
                    self._emit("rollback_finished", {"workflow_id": workflow_id, **rollback})
                except Exception as exc:
                    rollback = {"status": "failed", "error": str(exc), "backup_id": backup_id}
                    self._emit("rollback_finished", {"workflow_id": workflow_id, **rollback})
            workflow["rollback"] = rollback
            if rollback.get("status") == "rolled_back":
                workflow.update({"status": "rolled_back", "phase": "rolled_back"})
            else:
                workflow.update({"status": "failed", "phase": "verification_failed"})

        _atomic_json(state_path, workflow)
        self._emit(
            "coding_workflow_finished",
            {"workflow_id": workflow_id, "status": workflow["status"], "phase": workflow["phase"]},
        )
        return _bounded_json(workflow)

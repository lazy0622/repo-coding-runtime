#!/usr/bin/env python3
"""Run deterministic Repo Coding Runtime V2 Supervisor demos."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext  # noqa: E402


def _build_agent(workspace_root, outputs):
    workspace = WorkspaceContext.build(workspace_root, repo_root_override=workspace_root)
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=SessionStore(workspace_root / ".pico" / "sessions"),
        approval_policy="auto",
        max_steps=5,
        enable_subagents=True,
    )


def _task_payload(isolate_worktrees=False):
    return {
        "goal": "Understand the demo service before editing it",
        "tasks": [
            {
                "id": "outline",
                "title": "Outline the service",
                "prompt": "Use Repo Index to find the Service class and its public methods.",
            },
            {
                "id": "risk",
                "title": "Assess implementation risks",
                "prompt": "Summarize coupling and likely test risks using the outline evidence.",
                "depends_on": ["outline"],
            },
        ],
        "isolate_worktrees": isolate_worktrees,
    }


def _run_success_case(output_root):
    workspace_root = output_root / "supervisor-success"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "service.py").write_text(
        "class Service:\n    def run(self):\n        return 'ok'\n",
        encoding="utf-8",
    )
    payload = _task_payload()
    tool_call = '<tool>' + json.dumps({"name": "run_task_graph", "args": payload}, ensure_ascii=False) + "</tool>"
    agent = _build_agent(
        workspace_root,
        [
            tool_call,
            "<final>Service exposes one synchronous run method.</final>",
            "<final>The implementation is small; test the public method before changing it.</final>",
            "<final>Supervisor completed the read-only research graph.</final>",
        ],
    )
    try:
        final_answer = agent.ask("Use V2 Supervisor to inspect the service")
        run_dir = Path(agent.current_run_dir)
        return {
            "name": "supervisor-success",
            "final_answer": final_answer,
            "status": agent.current_task_state.status,
            "run_dir": str(run_dir),
            "subagent_graph": str(run_dir / "subagents"),
        }
    finally:
        agent.close()


def _run_fallback_case(output_root):
    workspace_root = output_root / "worktree-fallback"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
    agent = _build_agent(
        workspace_root,
        [
            "<final>Read-only inspection complete.</final>",
            "<final>No Git repository was available for a detached worktree.</final>",
        ],
    )
    try:
        result = agent.execute_tool("run_task_graph", _task_payload(isolate_worktrees=True))
        payload = json.loads(result.content)
        return {
            "name": "worktree-fallback",
            "tool_status": result.metadata["tool_status"],
            "isolation_mode": payload["isolation_mode"],
            "fallback_reason": payload["isolation_fallback_reason"],
            "artifact_dir": payload["artifact_dir"],
        }
    finally:
        agent.close()


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run Repo Coding Runtime V2 Supervisor demos.")
    parser.add_argument(
        "--output-dir",
        default="artifacts/v2_demos",
        help="Directory for demo workspaces and run artifacts.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    output_root = Path(args.output_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    cases = [_run_success_case(output_root), _run_fallback_case(output_root)]
    (output_root / "summary.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_root": str(output_root), "cases": cases}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

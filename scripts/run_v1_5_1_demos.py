#!/usr/bin/env python3
"""Run three deterministic demos for the Repo Coding Runtime V1.5.1 review pack."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext  # noqa: E402
from pico.command_runner import format_python_command  # noqa: E402
from pico.replay import render_replay  # noqa: E402


def _build_agent(workspace_root, outputs, *, verify_command="", approval_policy="auto"):
    workspace = WorkspaceContext.build(workspace_root, repo_root_override=workspace_root)
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=SessionStore(workspace_root / ".pico" / "sessions"),
        approval_policy=approval_policy,
        max_steps=6,
        verify_command=verify_command,
        max_verification_attempts=1,
    )


def _run_case(output_root, name, initial_text, outputs, *, verify_command="", approval_policy="auto"):
    workspace_root = output_root / name
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "README.md").write_text(initial_text, encoding="utf-8")
    agent = _build_agent(
        workspace_root,
        outputs,
        verify_command=verify_command,
        approval_policy=approval_policy,
    )
    try:
        final_answer = agent.ask(name.replace("-", " "))
        run_id = agent.current_task_state.run_id
        run_dir = Path(agent.current_run_dir)
        report = agent.run_store.load_report(run_id)
        replay_path = run_dir / "replay.txt"
        replay_path.write_text(render_replay(run_dir / "trace.jsonl", include_json=True) + "\n", encoding="utf-8")
        return {
            "name": name,
            "final_answer": final_answer,
            "status": agent.current_task_state.status,
            "stop_reason": agent.current_task_state.stop_reason,
            "verification": report.get("verification", {}),
            "workspace": str(workspace_root),
            "run_dir": str(run_dir),
            "task_state": str(run_dir / "task_state.json"),
            "trace": str(run_dir / "trace.jsonl"),
            "report": str(run_dir / "report.json"),
            "replay": str(replay_path),
        }
    finally:
        agent.close()


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run Repo Coding Runtime V1.5.1 deterministic demos.")
    parser.add_argument(
        "--output-dir",
        default="artifacts/v1_5_1_demos",
        help="Directory for demo workspaces and run artifacts.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    output_root = Path(args.output_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)

    verify_status = format_python_command(
        "from pathlib import Path; assert 'status: verified' in Path('README.md').read_text(encoding='utf-8')"
    )
    verify_exists = format_python_command("from pathlib import Path; assert Path('README.md').is_file()")

    cases = [
        _run_case(
            output_root,
            "plan-verify",
            "# V1.5.1 Demo\n\nstatus: pending\n",
            [
                '<plan>{"goal":"Update and verify README status","tasks":[{"id":"status","title":"Update status"}]}</plan>',
                '<tool name="patch_file" path="README.md"><old_text>status: pending</old_text><new_text>status: verified</new_text></tool>',
                "<final>Plan executed and verification passed.</final>",
            ],
            verify_command=verify_status,
        ),
        _run_case(
            output_root,
            "safety-boundary",
            "# V1.5.1 Safety Demo\n",
            [
                '<tool>{"name":"run_shell","args":{"command":"git reset --hard HEAD","timeout":20}}</tool>',
                "<final>Destructive command was blocked by the safety policy.</final>",
            ],
        ),
        _run_case(
            output_root,
            "replay-evidence",
            "# V1.5.1 Replay Demo\n",
            [
                '<plan>{"goal":"Inspect README and preserve evidence","tasks":[{"id":"inspect","title":"Inspect README"}]}</plan>',
                '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":20}}</tool>',
                "<final>README inspected; replay artifacts are available.</final>",
            ],
            verify_command=verify_exists,
        ),
    ]

    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_root": str(output_root), "cases": cases}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

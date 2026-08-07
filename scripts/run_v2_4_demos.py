#!/usr/bin/env python3
"""Run deterministic Repo Coding Runtime V2.1-V2.4 evidence and coding workflow demos."""

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


RESEARCH_OUTPUT = (
    '<final>{"summary":"Located Service.run.",'
    '"findings":["The service returns a fixed value."],'
    '"evidence":[{"path":"service.py","line_start":1,"line_end":3,'
    '"symbol":"Service.run","claim":"run returns ok","confidence":0.95}],'
    '"risks":[],"recommendations":["Keep the patch local"],"confidence":0.95}</final>'
)

PATCH = """--- a/service.py
+++ b/service.py
@@ -1,3 +1,3 @@
 class Service:
     def run(self):
-        return 'ok'
+        return 'fixed'
"""


def _build_agent(workspace_root):
    return Pico(
        model_client=FakeModelClient([RESEARCH_OUTPUT]),
        workspace=WorkspaceContext.build(workspace_root, repo_root_override=workspace_root),
        session_store=SessionStore(workspace_root / ".pico" / "sessions"),
        approval_policy="auto",
        enable_subagents=True,
    )


def _workflow_args(verify_command):
    return {
        "goal": "Update the service result",
        "research_tasks": [
            {
                "id": "inspect",
                "title": "Inspect service",
                "prompt": "Locate Service.run and report line evidence.",
            }
        ],
        "patch": PATCH,
        "verify_command": verify_command,
    }


def _prepare_workspace(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "service.py").write_text(
        "class Service:\n    def run(self):\n        return 'ok'\n",
        encoding="utf-8",
    )


def _run_success(output_root):
    root = output_root / "workflow-success"
    _prepare_workspace(root)
    agent = _build_agent(root)
    verify = format_python_command(
        "from pathlib import Path; assert 'fixed' in Path('service.py').read_text()"
    )
    try:
        result = agent.execute_tool("run_coding_workflow", _workflow_args(verify))
        payload = json.loads(result.content)
        assert payload["status"] == "completed"
        assert payload["research_status"] == "completed"
        assert payload["verification"]["passed"] is True
        assert "return 'fixed'" in (root / "service.py").read_text(encoding="utf-8")
        return {
            "name": "workflow-success",
            "tool_status": result.metadata["tool_status"],
            "status": payload["status"],
            "workflow_id": payload["workflow_id"],
            "artifact_dir": payload["artifact_dir"],
        }
    finally:
        agent.close()


def _run_rollback(output_root):
    root = output_root / "workflow-rollback"
    _prepare_workspace(root)
    agent = _build_agent(root)
    verify = format_python_command("import sys; sys.exit(1)")
    try:
        result = agent.execute_tool("run_coding_workflow", _workflow_args(verify))
        payload = json.loads(result.content)
        assert payload["status"] == "rolled_back"
        assert payload["rollback"]["status"] == "rolled_back"
        assert "return 'ok'" in (root / "service.py").read_text(encoding="utf-8")
        return {
            "name": "workflow-rollback",
            "tool_status": result.metadata["tool_status"],
            "status": payload["status"],
            "workflow_id": payload["workflow_id"],
            "artifact_dir": payload["artifact_dir"],
        }
    finally:
        agent.close()


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run Repo Coding Runtime V2.1-V2.4 demos.")
    parser.add_argument(
        "--output-dir",
        default="artifacts/v2_4_demos",
        help="Directory for demo workspaces and artifacts.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    output_root = Path(args.output_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    cases = [_run_success(output_root), _run_rollback(output_root)]
    summary = {"output_root": str(output_root), "cases": cases}
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

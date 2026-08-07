import json

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.command_runner import format_python_command


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


def build_agent(tmp_path, outputs):
    (tmp_path / "service.py").write_text(
        "class Service:\n    def run(self):\n        return 'ok'\n",
        encoding="utf-8",
    )
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
        enable_subagents=True,
    )


def workflow_args(verify_command):
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


def test_v24_coding_workflow_researches_patches_and_verifies(tmp_path):
    agent = build_agent(tmp_path, [RESEARCH_OUTPUT])
    verify = format_python_command(
        "from pathlib import Path; assert 'fixed' in Path('service.py').read_text()"
    )

    result = agent.execute_tool("run_coding_workflow", workflow_args(verify))
    payload = json.loads(result.content)

    assert result.metadata["tool_status"] == "ok"
    assert result.metadata["workspace_changed"] is True
    assert payload["status"] == "completed"
    assert payload["research_status"] == "completed"
    assert payload["verification"]["passed"] is True
    assert payload["patch"]["backup_id"]
    assert (tmp_path / "service.py").read_text(encoding="utf-8").endswith("return 'fixed'\n")
    artifact = tmp_path / ".pico" / "workflows" / agent.session["id"] / payload["workflow_id"] / "workflow.json"
    assert artifact.is_file()
    workflow = json.loads(artifact.read_text(encoding="utf-8"))
    assert workflow["research"]["status"] == "completed"


def test_v24_coding_workflow_rolls_back_when_verification_fails(tmp_path):
    agent = build_agent(tmp_path, [RESEARCH_OUTPUT])
    verify = format_python_command("import sys; sys.exit(1)")

    result = agent.execute_tool("run_coding_workflow", workflow_args(verify))
    payload = json.loads(result.content)

    assert result.metadata["tool_status"] == "partial_success"
    assert result.metadata["tool_error_code"] == "coding_workflow_rolled_back"
    assert payload["status"] == "rolled_back"
    assert payload["rollback"]["status"] == "rolled_back"
    assert "return 'ok'" in (tmp_path / "service.py").read_text(encoding="utf-8")


def test_v24_coding_workflow_does_not_patch_after_research_failure(tmp_path):
    agent = build_agent(tmp_path, [])
    verify = format_python_command("from pathlib import Path; assert False")

    result = agent.execute_tool("run_coding_workflow", workflow_args(verify))
    payload = json.loads(result.content)

    assert result.metadata["tool_status"] == "error"
    assert payload["status"] == "failed"
    assert payload["phase"] == "research_failed"
    assert "return 'ok'" in (tmp_path / "service.py").read_text(encoding="utf-8")

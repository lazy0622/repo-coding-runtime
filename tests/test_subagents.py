import json
import sys
import time

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.subagents import SubAgentManager
from pico.task_graph import TaskGraph


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


def graph_args(**extra):
    payload = {
        "goal": "Understand the service",
        "tasks": [
            {
                "id": "outline",
                "title": "Outline service",
                "prompt": "Find the Service class and report its methods.",
            },
            {
                "id": "summary",
                "title": "Summarize service",
                "prompt": "Summarize the implementation and risks.",
                "depends_on": ["outline"],
            },
        ],
    }
    payload.update(extra)
    return payload


def test_v2_supervisor_runs_dependency_graph_and_persists_artifacts(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>Service has one run method.</final>",
            "<final>The service is a small synchronous wrapper.</final>",
        ],
    )

    result = agent.execute_tool("run_task_graph", graph_args())
    payload = json.loads(result.content)

    assert result.metadata["tool_status"] == "ok"
    assert result.metadata["read_only"] is True
    assert payload["status"] == "completed"
    assert [item["status"] for item in payload["tasks"]] == ["completed", "completed"]
    artifact = tmp_path / ".pico" / "subagents" / agent.session["id"] / payload["graph_id"] / "task_graph.json"
    assert artifact.is_file()
    assert "Service has one run method" in agent.model_client.prompts[1]


def test_v2_supervisor_falls_back_safely_without_git_and_records_lifecycle(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            "<final>Read-only inspection complete.</final>",
            "<final>Summary complete.</final>",
        ],
    )

    state = agent.current_task_state
    result = agent.execute_tool("run_task_graph", {**graph_args(), "isolate_worktrees": True})
    payload = json.loads(result.content)

    assert payload["isolation_mode"] == "read_only_shared_workspace"
    assert "worktree isolation unavailable" in payload["isolation_fallback_reason"]
    assert state is None


def test_v2_supervisor_marks_failed_graph_and_blocks_dependents(tmp_path):
    agent = build_agent(tmp_path, [])

    result = agent.execute_tool("run_task_graph", graph_args())
    payload = json.loads(result.content)

    assert result.metadata["tool_status"] == "error"
    assert payload["status"] == "failed"
    assert [item["status"] for item in payload["tasks"]] == ["failed", "blocked"]


def test_v2_tool_is_not_visible_without_explicit_enablement(tmp_path):
    agent = Pico(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
    )

    assert "run_task_graph" not in agent.tools
    assert "run_coding_workflow" not in agent.tools


def test_v21_supervisor_normalizes_structured_evidence_and_summarizes(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<final>{"summary":"Service exposes run.","findings":["The service is synchronous."],"evidence":[{"path":"service.py","line_start":1,"line_end":2,"symbol":"Service.run","claim":"run returns ok","confidence":0.9}],"risks":["No error handling"],"recommendations":["Add a test"],"confidence":0.9}</final>',
            '<final>{"summary":"The public method is small.","findings":["A focused test is sufficient."],"evidence":[{"path":"service.py","line_start":2,"line_end":3,"symbol":"Service.run","claim":"method is easy to test","confidence":0.8}],"recommendations":["Keep the change local"],"confidence":0.8}</final>',
        ],
    )

    result = agent.execute_tool("run_task_graph", graph_args())
    payload = json.loads(result.content)

    assert payload["confidence"] == 0.85
    assert "No error handling" in payload["risks"]
    assert len(payload["evidence"]) == 2
    assert payload["tasks"][0]["evidence"]["parsed"] is True
    assert "Structured evidence from completed dependencies" in agent.model_client.prompts[1]
    assert "run returns ok" in agent.model_client.prompts[1]


def test_v22_supervisor_retries_failed_child(tmp_path):
    class FlakyModel:
        model = "flaky-test"
        supports_prompt_cache = False

        def __init__(self):
            self.prompts = []
            self.last_completion_metadata = {}
            self.calls = 0

        def complete(self, prompt, max_new_tokens, **kwargs):
            self.prompts.append(prompt)
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient provider failure")
            return "<final>Recovered.</final>"

    agent = Pico(
        model_client=FlakyModel(),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
        enable_subagents=True,
    )

    result = agent.execute_tool(
        "run_task_graph",
        {
            "goal": "Retry research",
            "max_task_attempts": 2,
            "tasks": [{"id": "inspect", "title": "Inspect", "prompt": "Inspect service."}],
        },
    )
    payload = json.loads(result.content)

    assert payload["status"] == "completed"
    assert payload["tasks"][0]["attempts"] == 2
    assert payload["tasks"][0]["retry_history"][0]["reason"] == "child_failure"


def test_v22_supervisor_reports_timeout(tmp_path):
    class SlowModel:
        model = "slow-test"
        supports_prompt_cache = False

        def __init__(self):
            self.prompts = []
            self.last_completion_metadata = {}

        def complete(self, prompt, max_new_tokens, **kwargs):
            self.prompts.append(prompt)
            time.sleep(1.2)
            return "<final>Too late.</final>"

    agent = Pico(
        model_client=SlowModel(),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
        enable_subagents=True,
    )

    result = agent.execute_tool(
        "run_task_graph",
        {
            "goal": "Timeout research",
            "task_timeout_seconds": 1,
            "tasks": [{"id": "slow", "title": "Slow", "prompt": "Inspect slowly."}],
        },
    )
    payload = json.loads(result.content)

    assert payload["status"] == "failed"
    assert "timeout" in payload["tasks"][0]["error"]


def test_v22_supervisor_resumes_persisted_graph(tmp_path):
    agent = build_agent(tmp_path, ["<final>Resumed evidence.</final>"])
    graph_id = "resume-test"
    graph = TaskGraph.from_mapping(
        {"graph_id": graph_id, "tasks": [{"id": "inspect", "prompt": "Inspect", "max_attempts": 2}]}
    )
    graph.mark_running("inspect")
    root = SubAgentManager(agent)._artifact_root_for_id(graph_id)
    SubAgentManager._write_state(root / "task_graph.json", graph, {"resume_count": 0})

    result = agent.execute_tool(
        "run_task_graph",
        {"resume": True, "graph_id": graph_id, "max_steps": 2},
    )
    payload = json.loads(result.content)

    assert payload["resumed"] is True
    assert payload["recovered_tasks"] == ["inspect"]
    assert payload["status"] == "completed"

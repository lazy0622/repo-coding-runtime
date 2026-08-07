import json
from unittest.mock import patch

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.task_state import PHASE_EXECUTING, PHASE_WAITING_APPROVAL, TaskState
from pico.tool_executor import ToolExecutor, ToolExecutionResult


def build_agent(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    return Pico(
        model_client=FakeModelClient([]),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
    )


def test_tool_executor_returns_content_and_metadata_without_side_channel(tmp_path):
    agent = build_agent(tmp_path)

    result = ToolExecutor(agent).execute("read_file", {"path": "README.md", "start": 1, "end": 1})

    assert isinstance(result, ToolExecutionResult)
    assert "# README.md" in result.content
    assert result.metadata["tool_status"] == "ok"
    assert result.metadata["read_only"] is True
    assert result.metadata["workspace_changed"] is False


def test_pico_run_tool_keeps_compatibility_metadata(tmp_path):
    agent = build_agent(tmp_path)

    content = agent.run_tool("read_file", {"path": "README.md", "start": 1, "end": 1})

    assert "# README.md" in content
    assert agent._last_tool_result_metadata["tool_status"] == "ok"


def test_tool_gateway_emits_canonical_policy_and_lifecycle_events(tmp_path):
    agent = build_agent(tmp_path)
    state = TaskState.create(run_id="run_gateway", task_id="task_gateway", user_request="Read.")
    state.transition(PHASE_EXECUTING)
    agent.current_task_state = state
    agent.run_store.start_run(state)

    agent.execute_tool("read_file", {"path": "README.md", "start": 1, "end": 1})

    events = [json.loads(line) for line in agent.run_store.trace_path(state).read_text(encoding="utf-8").splitlines()]
    names = [event["event"] for event in events]
    assert names == ["tool_requested", "policy_decided", "tool_started", "tool_finished"]
    assert [event["event_type"] for event in events] == [
        "tool.requested",
        "policy.decided",
        "tool.started",
        "tool.finished",
    ]


def test_tool_gateway_records_waiting_approval_before_denial(tmp_path):
    agent = build_agent(tmp_path)
    agent.approval_policy = "ask"
    state = TaskState.create(run_id="run_approval", task_id="task_approval", user_request="Write.")
    state.transition(PHASE_EXECUTING)
    agent.current_task_state = state
    agent.run_store.start_run(state)

    with patch("builtins.input", return_value="n"):
        result = agent.execute_tool("write_file", {"path": "new.txt", "content": "no"})

    assert result.metadata["tool_status"] == "rejected"
    assert state.phase == PHASE_WAITING_APPROVAL
    assert any(item["phase"] == PHASE_WAITING_APPROVAL for item in state.phase_history)

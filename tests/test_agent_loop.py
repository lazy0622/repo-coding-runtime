import json

import pytest

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.agent_loop import AgentLoop


def build_agent(tmp_path, outputs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".pico" / "sessions")
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
    )


def test_agent_loop_runs_same_control_flow_as_pico_ask(tmp_path):
    (tmp_path / "hello.txt").write_text("alpha\n", encoding="utf-8")
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"hello.txt","start":1,"end":1}}</tool>',
            "<final>Done.</final>",
        ],
    )

    answer = AgentLoop(agent).run("Inspect hello.txt")

    assert answer == "Done."
    assert agent.current_task_state.status == "completed"
    assert agent.run_store.report_path(agent.current_task_state.run_id).exists()


def test_pico_ask_delegates_to_agent_loop(tmp_path):
    agent = build_agent(tmp_path, ["<final>Facade works.</final>"])

    assert agent.ask("Use facade") == "Facade works."


class FailingModelClient:
    model = "failing-model"
    supports_prompt_cache = False

    def complete(self, *args, **kwargs):
        raise RuntimeError("provider unavailable")


def test_agent_loop_persists_failed_phase_and_event_on_model_error(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    agent = Pico(
        model_client=FailingModelClient(),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        agent.ask("Inspect the repo")

    state = agent.current_task_state
    assert state.status == "failed"
    assert state.phase == "failed"
    report = json.loads(agent.run_store.report_path(state).read_text(encoding="utf-8"))
    events = [json.loads(line) for line in agent.run_store.trace_path(state).read_text(encoding="utf-8").splitlines()]
    assert report["stop_reason"] == "model_error"
    assert events[-1]["event"] == "model_failed"

import json

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.plan import PLAN_COMPLETED, TASK_COMPLETED


def build_agent(tmp_path, outputs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
    )


def test_model_plan_is_persisted_and_serially_completed(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<plan>{"goal":"Inspect and finish","tasks":[{"id":"inspect","title":"Inspect the repository"},{"id":"finish","title":"Finish the task","depends_on":["inspect"]}]}</plan>',
            "<final>Inspection complete.</final>",
            "<final>Task complete.</final>",
        ],
    )

    assert agent.ask("Inspect and finish") == "Task complete."

    plan = agent.current_plan()
    assert plan.status == PLAN_COMPLETED
    assert [task.status for task in plan.tasks] == [TASK_COMPLETED, TASK_COMPLETED]
    assert agent.current_task_state.plan_id == plan.plan_id
    assert json.loads(agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8"))["plan"]["status"] == PLAN_COMPLETED

    events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "plan_updated" for event in events)
    assert sum(event["event"] == "plan_task_completed" for event in events) == 1


def test_plan_state_is_included_in_checkpoint(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"])

    agent.ask("Complete the task")

    checkpoint = agent.current_checkpoint()
    assert checkpoint["plan"]["plan_id"] == agent.current_plan().plan_id
    assert checkpoint["plan"]["status"] == PLAN_COMPLETED


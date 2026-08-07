import pytest

from pico.task_graph import (
    TASK_RUNNING,
    TASK_BLOCKED,
    TASK_COMPLETED,
    TASK_FAILED,
    TaskGraph,
    TaskGraphError,
)


def test_task_graph_schedules_dependencies_and_blocks_dependents():
    graph = TaskGraph.from_mapping(
        {
            "goal": "Inspect runtime",
            "tasks": [
                {"id": "outline", "prompt": "Outline the runtime."},
                {"id": "dependencies", "prompt": "Inspect imports.", "depends_on": ["outline"]},
                {"id": "summary", "prompt": "Summarize.", "depends_on": ["dependencies"]},
            ],
        }
    )

    assert [task.task_id for task in graph.ready_tasks()] == ["outline"]
    graph.mark_running("outline")
    graph.mark_completed("outline", "outline evidence")
    assert [task.task_id for task in graph.ready_tasks()] == ["dependencies"]
    graph.mark_running("dependencies")
    graph.mark_failed("dependencies", "child unavailable")

    summary = graph.task("summary")
    assert summary.status == TASK_BLOCKED
    assert graph.task("dependencies").status == TASK_FAILED
    assert graph.status == "failed"
    assert graph.is_terminal()


def test_task_graph_completes_independent_fan_out():
    graph = TaskGraph.from_mapping(
        {"tasks": [{"id": "a", "prompt": "A"}, {"id": "b", "prompt": "B"}]}
    )
    for task_id in ("a", "b"):
        graph.mark_running(task_id)
        graph.mark_completed(task_id, task_id)

    assert graph.status == "completed"
    assert all(task.status == TASK_COMPLETED for task in graph.tasks)


@pytest.mark.parametrize(
    "tasks, message",
    [
        ([{"id": "a", "prompt": "A"}, {"id": "a", "prompt": "A2"}], "unique"),
        ([{"id": "a", "prompt": "A", "depends_on": ["missing"]}], "unknown"),
        ([{"id": "a", "prompt": "A", "depends_on": ["b"]}, {"id": "b", "prompt": "B", "depends_on": ["a"]}], "cycle"),
    ],
)
def test_task_graph_rejects_invalid_graphs(tasks, message):
    with pytest.raises(TaskGraphError, match=message):
        TaskGraph.from_mapping({"tasks": tasks})


def test_task_graph_round_trip_recovers_interrupted_task():
    graph = TaskGraph.from_mapping(
        {"tasks": [{"id": "inspect", "prompt": "Inspect", "max_attempts": 2}]}
    )
    graph.mark_running("inspect")
    assert graph.task("inspect").status == TASK_RUNNING

    restored = TaskGraph.from_dict(graph.to_dict())
    recovered = restored.recover_running_tasks()

    assert recovered == ["inspect"]
    assert restored.task("inspect").status == "pending"
    assert restored.task("inspect").attempts == 1
    assert restored.task("inspect").retry_history[-1]["reason"] == "graph_resume"


def test_task_graph_retry_preserves_attempt_history():
    graph = TaskGraph.from_mapping(
        {"tasks": [{"id": "inspect", "prompt": "Inspect", "max_attempts": 2}]}
    )

    graph.mark_running("inspect")
    graph.mark_retry("inspect", "provider unavailable", reason="child_failure")

    assert graph.task("inspect").status == "pending"
    assert graph.task("inspect").can_retry() is True
    assert graph.task("inspect").retry_history[0]["error"] == "provider unavailable"

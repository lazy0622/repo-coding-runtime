from pico.events import build_run_event, canonical_event_type
from pico.task_state import TaskState


def test_event_schema_preserves_legacy_name_and_adds_stable_identity():
    state = TaskState.create(run_id="run_001", task_id="task_001", user_request="Inspect.")

    event = build_run_event(state, "tool_started", "2026-08-04T00:00:00Z", {"name": "read_file"})

    assert event["event"] == "tool_started"
    assert event["event_type"] == "tool.started"
    assert event["event_schema_version"] == 1
    assert event["run_id"] == "run_001"
    assert event["task_id"] == "task_001"
    assert event["phase"] == "created"
    assert event["name"] == "read_file"


def test_canonical_event_type_accepts_already_dotted_names():
    assert canonical_event_type("policy.decided") == "policy.decided"

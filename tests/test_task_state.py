from pico.task_state import (
    PHASE_COMPLETED,
    PHASE_EXECUTING,
    PHASE_PLANNING,
    STOP_REASON_FINAL_ANSWER_RETURNED,
    STOP_REASON_RETRY_LIMIT_REACHED,
    STOP_REASON_STEP_LIMIT_REACHED,
    TaskState,
)


def test_task_state_starts_running_with_empty_progress():
    state = TaskState.create(run_id="run_001", task_id="task_001", user_request="Inspect the repo.")

    assert state.task_id == "task_001"
    assert state.run_id == "run_001"
    assert state.user_request == "Inspect the repo."
    assert state.status == "running"
    assert state.tool_steps == 0
    assert state.attempts == 0
    assert state.last_tool == ""
    assert state.stop_reason == ""
    assert state.final_answer == ""


def test_task_state_records_success_and_final_answer():
    state = TaskState.create(run_id="run_002", task_id="task_002", user_request="Fix the bug.")
    state.record_attempt()
    state.record_tool("read_file")
    state.finish_success("Done.")

    assert state.attempts == 1
    assert state.tool_steps == 1
    assert state.last_tool == "read_file"
    assert state.status == "completed"
    assert state.stop_reason == STOP_REASON_FINAL_ANSWER_RETURNED
    assert state.final_answer == "Done."


def test_task_state_records_step_limit_stop_reason():
    state = TaskState.create(run_id="run_003", task_id="task_003", user_request="Try again.")

    state.stop_step_limit()

    assert state.status == "stopped"
    assert state.stop_reason == STOP_REASON_STEP_LIMIT_REACHED


def test_task_state_records_retry_limit_stop_reason():
    state = TaskState.create(run_id="run_004", task_id="task_004", user_request="Try again.")

    state.stop_retry_limit()

    assert state.status == "stopped"
    assert state.stop_reason == STOP_REASON_RETRY_LIMIT_REACHED


def test_task_state_snapshot_keeps_final_answer():
    state = TaskState.create(run_id="run_005", task_id="task_005", user_request="Return the answer.")
    state.finish_success("Final answer.")

    snapshot = state.to_dict()

    assert snapshot["final_answer"] == "Final answer."
    assert snapshot["stop_reason"] == STOP_REASON_FINAL_ANSWER_RETURNED


def test_task_state_snapshot_keeps_checkpoint_reference_without_body():
    state = TaskState.create(run_id="run_006", task_id="task_006", user_request="Resume the task.")
    state.checkpoint_id = "ckpt_001"
    state.resume_status = "full-valid"

    snapshot = state.to_dict()

    assert snapshot["checkpoint_id"] == "ckpt_001"
    assert snapshot["resume_status"] == "full-valid"
    assert "current_goal" not in snapshot
    assert "next_step" not in snapshot


def test_task_state_records_legal_phase_transitions_and_terminal_phase():
    state = TaskState.create(run_id="run_phase", task_id="task_phase", user_request="Work.")

    state.transition(PHASE_PLANNING, reason="run_started")
    state.transition(PHASE_EXECUTING, reason="tool:read_file")
    state.finish_success("Done.")

    assert state.phase == PHASE_COMPLETED
    assert [item["phase"] for item in state.phase_history] == ["created", "planning", "executing", "completed"]
    assert state.phase_history[-1]["reason"] == STOP_REASON_FINAL_ANSWER_RETURNED


def test_task_state_rejects_illegal_phase_transition():
    state = TaskState.create(run_id="run_bad_phase", task_id="task_bad_phase", user_request="Work.")
    state.finish_success("Done.")

    try:
        state.transition(PHASE_PLANNING)
    except ValueError as exc:
        assert "completed -> planning" in str(exc)
    else:
        raise AssertionError("terminal task phase accepted a new transition")


def test_task_state_migrates_legacy_terminal_snapshot_to_terminal_phase():
    state = TaskState.from_dict(
        {
            "run_id": "run_legacy",
            "task_id": "task_legacy",
            "user_request": "Old task.",
            "status": "completed",
        }
    )

    assert state.phase == PHASE_COMPLETED

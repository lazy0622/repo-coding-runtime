import json
import sys

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.task_state import STOP_REASON_VERIFICATION_FAILED
from pico.verification import VERIFY_FAILED, VERIFY_PASSED, VERIFY_SKIPPED, run_verification


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return Pico(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )


def test_verification_pass_is_recorded_before_final_completion(tmp_path):
    agent = build_agent(tmp_path, ["<final>Done.</final>"], verify_command="echo verify")

    assert agent.ask("Run the verifier") == "Done."

    report = json.loads(agent.run_store.report_path(agent.current_task_state).read_text(encoding="utf-8"))
    assert report["verification"]["status"] == VERIFY_PASSED
    assert report["verification"]["passed"] is True
    assert agent.current_task_state.verification_attempts == 1


def test_successful_edit_triggers_immediate_verification_once(tmp_path):
    agent = build_agent(
        tmp_path,
        [
            '<tool name="patch_file" path="README.md"><old_text>demo</old_text><new_text>updated</new_text></tool>',
            "<final>Done.</final>",
        ],
        task_mode="edit",
        verify_command="echo verify",
        max_steps=2,
    )

    assert agent.ask("Update README.md") == "Done."
    assert agent.current_task_state.final_verifier_passed is True
    assert agent.current_task_state.verification_attempts == 1
    events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(event["event"] == "verification_started" for event in events) == 1
    assert sum(event["event"] == "verification_finished" for event in events) == 1


def test_verification_failure_retries_then_stops_with_evidence(tmp_path):
    failing_command = f'"{sys.executable}" -c "import sys; sys.exit(1)"'
    agent = build_agent(
        tmp_path,
        ["<final>First answer.</final>", "<final>Second answer.</final>"],
        verify_command=failing_command,
        max_verification_attempts=1,
    )

    answer = agent.ask("Run the failing verifier")

    assert "Verification failed after 2 attempt(s)" in answer
    assert agent.current_task_state.stop_reason == STOP_REASON_VERIFICATION_FAILED
    assert agent.current_task_state.verification_attempts == 2
    events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state).read_text(encoding="utf-8").splitlines()
    ]
    assert sum(event["event"] == "verification_finished" for event in events) == 2
    assert any(event["event"] == "verification_retry" for event in events)


def test_verification_without_command_is_skipped(tmp_path):
    result = run_verification(tmp_path, "")

    assert result.status == VERIFY_SKIPPED
    assert result.passed is False


def test_opt_in_verified_stop_does_not_request_more_model_actions(tmp_path):
    agent = build_agent(
        tmp_path,
        ['<tool name="patch_file" path="README.md"><old_text>demo</old_text><new_text>updated</new_text></tool>'],
        task_mode="edit", plan_mode=False, verify_command="echo verified",
        stop_after_verification=True, max_steps=2,
    )
    agent.ask("Update README.md")
    assert agent.current_task_state.status == "completed"
    assert agent.current_task_state.stop_reason == "configured_verification_passed"
    assert agent.current_task_state.attempts == 1
    assert agent.current_task_state.final_verifier_passed is True


def test_verified_stop_never_completes_after_a_failed_check(tmp_path):
    agent = build_agent(
        tmp_path,
        ['<tool name="patch_file" path="README.md"><old_text>demo</old_text><new_text>updated</new_text></tool>'],
        task_mode="edit", plan_mode=False,
        verify_command=f'"{sys.executable}" -c "import sys;sys.exit(3)"',
        stop_after_verification=True, max_verification_attempts=0, max_steps=2,
    )
    agent.ask("Update README.md")
    assert agent.current_task_state.stop_reason == "verification_failed"
    assert agent.current_task_state.final_verifier_passed is False


def test_verification_nonzero_exit_is_failed(tmp_path):
    command = f'"{sys.executable}" -c "import sys; sys.exit(2)"'

    result = run_verification(tmp_path, command, timeout=10, env=None)

    assert result.status == VERIFY_FAILED
    assert result.exit_code == 2


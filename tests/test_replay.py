import json

from pico.replay import load_trace, render_replay, summarize_trace


def test_replay_summarizes_tools_verification_and_state_changes(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                json.dumps({"event": "run_started"}),
                json.dumps({"event": "state_changed", "previous_phase": "planning", "current_phase": "executing", "reason": "tool:read_file"}),
                json.dumps({"event": "tool_finished", "name": "read_file", "tool_status": "ok", "duration_ms": 3}),
                json.dumps({"event": "verification_finished", "status": "passed", "passed": True, "exit_code": 0}),
                json.dumps({"event": "run_finished"}),
            ]
        ),
        encoding="utf-8",
    )

    events = load_trace(trace_path)
    summary = summarize_trace(events)
    replay = render_replay(trace_path)

    assert summary["event_count"] == 5
    assert summary["tool_calls"][0]["name"] == "read_file"
    assert summary["verification"][0]["passed"] is True
    assert "tool_finished read_file [ok]" in replay
    assert "Verification runs: 1" in replay


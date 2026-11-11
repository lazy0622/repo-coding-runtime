import json
import queue
import time
from types import SimpleNamespace

import pytest

from benchmarks.pi_compare import pi_agent


@pytest.mark.parametrize(
    "tail, expected, close_raises",
    [
        ([{"type": "agent_settled", "aborted": False}], "completed", False),
        ([{"type": "agent_end"}], "failed", False),
        ([{"type": "agent_settled", "aborted": True}], "failed", False),
        ([TimeoutError("deadline")], "failed", False),
        ([{"type": "agent_settled", "aborted": False}], "failed", True),
        (
            [
                {
                    "type": "message_end",
                    "message": {"role": "assistant", "stopReason": "error"},
                },
                {"type": "agent_settled", "aborted": False},
            ],
            "failed",
            False,
        ),
    ],
)
def test_acceptance_response_or_low_level_end_is_not_completion(
    tmp_path, monkeypatch, tail, expected, close_raises
):
    events = iter(
        [
            {
                "type": "response",
                "id": "task",
                "success": True,
                "data": {"disposition": "started"},
            },
            {
                "type": "message_end",
                "message": {"role": "assistant", "stopReason": "stop"},
            },
            *tail,
        ]
    )

    class FakeRPC:
        def request(self, *args, **kwargs):
            return {}

        def send(self, command):
            assert command["message"] == "--help"

        def receive(self, deadline):
            event = next(events, EOFError("closed"))
            if isinstance(event, Exception):
                raise event
            return event

        def close(self):
            if close_raises:
                raise OSError("Injected cleanup failure")

    monkeypatch.setattr(pi_agent, "launch", lambda *args, **kwargs: FakeRPC())
    args = SimpleNamespace(
        workspace=tmp_path, node=None, cli=None, shell=None, task="--help", timeout=1
    )
    assert pi_agent.run(args) == (0 if expected == "completed" else 1)
    report = json.loads(next(tmp_path.glob(".pico/runs/*/report.json")).read_text())
    assert report["status"] == expected
    if close_raises:
        assert report["stop_reason"] == "process_cleanup_failed"


@pytest.mark.parametrize("disposition", ["handled", "queued", None])
def test_single_task_prompt_must_start_before_any_completion(
    tmp_path, monkeypatch, disposition
):
    class FakeRPC:
        reads = 0

        def request(self, *args, **kwargs):
            return {}

        def send(self, command):
            pass

        def receive(self, deadline):
            self.reads += 1
            return {
                "type": "response",
                "id": "task",
                "success": True,
                "data": {"disposition": disposition},
            }

        def close(self):
            pass

    rpc = FakeRPC()
    monkeypatch.setattr(pi_agent, "launch", lambda *args, **kwargs: rpc)
    args = SimpleNamespace(
        workspace=tmp_path, node=None, cli=None, shell=None, task="task", timeout=1
    )
    assert pi_agent.run(args) == 1
    assert rpc.reads == 1


def test_extension_failure_is_not_swallowed_while_waiting_for_response():
    rpc = object.__new__(pi_agent.PiRPC)
    rpc.records = queue.Queue()
    rpc.responses = {}
    rpc.records.put({"type": "extension_error", "error": "spawn filter unavailable"})
    rpc.records.put({"type": "response", "id": "setup", "success": True})
    with pytest.raises(RuntimeError, match="extension failed"):
        rpc.wait_response("setup", timeout=1)


def test_interleaved_command_responses_are_correlated_by_id():
    rpc = object.__new__(pi_agent.PiRPC)
    rpc.records = queue.Queue()
    rpc.responses = {}
    rpc.records.put(
        {"type": "response", "id": "bash", "success": True, "data": {"cancelled": True}}
    )
    rpc.records.put({"type": "response", "id": "abort", "success": True})
    assert rpc.wait_response("abort", timeout=1) == {}
    assert rpc.wait_response("bash", timeout=0.1) == {"cancelled": True}


def test_rpc_receive_deadline_cannot_block_indefinitely():
    rpc = object.__new__(pi_agent.PiRPC)
    rpc.records = queue.Queue()
    with pytest.raises(TimeoutError):
        rpc.receive(time.monotonic() - 1)

import hashlib
import sys
import threading
import time

import pytest
from sqlalchemy import text

from pico.service.store import LeaseLost
from pico.service.worker import Worker, child_environment


def test_worker_produces_verified_patch_and_leaves_source_unchanged(store, configured):
    settings, job, count = configured
    assert Worker(settings, store).run_one() is True
    result = store.get(job["id"])
    assert result["state"] == "succeeded"
    assert result["result"]["verification_passed"] is True
    patch = settings.data_dir / result["checkpoint"]["patch_path"]
    assert b"return a+b" in patch.read_bytes()
    assert b"notes.txt" in patch.read_bytes()
    assert b"runtime-private" not in patch.read_bytes()
    assert b"do-not-publish" not in patch.read_bytes()
    assert (
        hashlib.sha256(patch.read_bytes()).hexdigest()
        == result["result"]["patch_sha256"]
    )
    assert (
        "return a-b"
        in (settings.repositories["demo"].path / "calculator.py").read_text()
    )
    assert count.read_text().count("executed") == 1


def test_crash_after_sealing_resumes_verification_without_agent_replay(
    store, configured
):
    settings, job, count = configured
    worker = Worker(settings, store, worker_id="old")
    original = worker._run

    def crash(command, cwd, output, *args, **kwargs):
        if output.name == "verification.log":
            raise SystemExit("Injected worker crash")
        return original(command, cwd, output, *args, **kwargs)

    worker._run = crash
    with pytest.raises(SystemExit):
        worker.run_one()
    assert store.get(job["id"])["state"] == "verifying"
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE runtime_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": job["id"]},
        )
    assert Worker(settings, store, worker_id="new").run_one()
    result = store.get(job["id"])
    assert result["state"] == "succeeded" and result["generation"] == 2
    assert result["result"]["recovery"] == "resume_verification"
    assert count.read_text().count("executed") == 1
    workspaces = [row["workspace"] for row in store.list_attempts(job["id"])]
    assert len(set(workspaces)) == 2
    with pytest.raises(LeaseLost):
        store.finish(job["id"], 1, "old", "failed", {})


def test_corrupted_checkpoint_is_rejected(store, configured):
    settings, job, _ = configured
    assert Worker(settings, store).run_one()
    result = store.get(job["id"])
    # Start a separate accepted job whose internal checkpoint is deliberately corrupted.
    other = store.submit(
        "corrupted",
        job["payload"] | {"task": "Fix another task"},
        base_commit=job["payload"]["base_commit"],
    )[0]
    claim = store.claim("crashed")
    checkpoint = dict(result["checkpoint"])
    checkpoint["sha256"] = "0" * 64
    store.checkpoint(other["id"], claim["generation"], "crashed", checkpoint)
    with store.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE runtime_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": other["id"]},
        )
    Worker(settings, store).run_one()
    failed = store.get(other["id"])
    assert failed["state"] == "failed"
    assert failed["result"]["reason"] == "ValueError"


def test_cancel_stops_running_subprocess(store, configured, tmp_path):
    settings, job, _ = configured
    marker = tmp_path / "started.txt"
    sleeper = tmp_path / "sleep.py"
    sleeper.write_text(
        f'from pathlib import Path\nimport time\nPath({str(marker)!r}).write_text("started")\ntime.sleep(30)\n'
    )
    settings.repositories["demo"].agent_command = [sys.executable, str(sleeper)]
    thread = threading.Thread(target=Worker(settings, store).run_one)
    started = time.monotonic()
    thread.start()
    try:
        while not marker.exists() and time.monotonic() - started < 10:
            time.sleep(0.05)
        assert marker.exists()
        store.cancel(job["id"])
        thread.join(timeout=10)
        assert not thread.is_alive()
        result = store.get(job["id"])
        assert result["state"] == "cancelled"
        assert result["result"]["process_exit_confirmed"] is True
    finally:
        if thread.is_alive():
            store.cancel(job["id"])
            thread.join(timeout=10)


def test_verifier_failure_is_not_success(store, configured):
    settings, job, _ = configured
    settings.repositories["demo"].verify_command = [
        sys.executable,
        "-c",
        "raise SystemExit(7)",
    ]
    Worker(settings, store).run_one()
    result = store.get(job["id"])
    assert result["state"] == "failed"
    assert result["result"]["verification_exit_code"] == 7


def test_development_checks_do_not_force_early_completion(store, configured):
    settings, job, _ = configured
    repository = settings.repositories["demo"]
    fixture_agent = repository.agent_command
    repository.agent_command = None
    repository.agent_verify_command = [sys.executable, "-c", "pass"]
    worker = Worker(settings, store)
    original = worker._run

    def execute(command, cwd, output, *args, **kwargs):
        if output.name == "agent.log":
            assert "--verify-command" in command
            assert "--stop-after-verification" not in command
            command = [arg.replace("{workspace}", str(cwd)) for arg in fixture_agent]
        return original(command, cwd, output, *args, **kwargs)

    worker._run = execute
    assert worker.run_one()
    assert store.get(job["id"])["state"] == "succeeded"


def test_child_environment_excludes_control_plane_credentials(monkeypatch):
    monkeypatch.setenv("RUNTIME_DATABASE_URL", "do-not-pass")
    monkeypatch.setenv("RUNTIME_API_TOKEN", "do-not-pass")
    monkeypatch.setenv("PICO_DEEPSEEK_API_KEY", "model-key")
    assert "RUNTIME_DATABASE_URL" not in child_environment(provider=True)
    assert "RUNTIME_API_TOKEN" not in child_environment(provider=True)
    assert child_environment(provider=True)["PICO_DEEPSEEK_API_KEY"] == "model-key"
    assert "PICO_DEEPSEEK_API_KEY" not in child_environment()


@pytest.mark.parametrize("task", ["--help", "--provider quoted 'value'\n中文 ; $(literal) {workspace} {task}"])
def test_registered_agent_receives_task_as_one_literal_argument(store, configured, task):
    from pathlib import Path

    settings, original_job, _ = configured
    repository = settings.repositories["demo"]
    agent_path = Path(repository.agent_command[1])
    with agent_path.open("a", encoding="utf-8") as handle:
        handle.write('root.joinpath("received-task.txt").write_text(sys.argv[2], encoding="utf-8")\n')
    repository.agent_command.append("{task}")
    store.cancel(original_job["id"])
    job = store.submit(
        "literal-task", original_job["payload"] | {"task": task},
        base_commit=original_job["payload"]["base_commit"],
    )[0]
    assert Worker(settings, store).run_one()
    assert store.get(job["id"])["state"] == "succeeded"
    workspace = Path(store.list_attempts(job["id"])[0]["workspace"])
    assert (workspace / "received-task.txt").read_text(encoding="utf-8") == task

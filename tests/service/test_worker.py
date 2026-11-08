import hashlib
import subprocess
import sys
import threading
import time

import pytest
from sqlalchemy import text

from pico.service.config import Repository, Settings
from pico.service.store import LeaseLost
from pico.service.worker import Worker, child_environment


@pytest.fixture
def configured(tmp_path, store):
    source = tmp_path / "source"
    source.mkdir()
    (source / "calculator.py").write_text("def add(a,b):\n    return a-b\n")
    (source / ".gitignore").write_text(".pico/\n__pycache__/\n")
    for args in [
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ]:
        subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
    count = tmp_path / "agent-count.txt"
    agent = tmp_path / "agent.py"
    agent.write_text(
        "from pathlib import Path\nimport sys\n"
        f'with Path({str(count)!r}).open("a") as h:h.write("executed\\n")\n'
        "root=Path(sys.argv[1])\n"
        'root.joinpath("calculator.py").write_text("def add(a,b):\\n    return a+b\\n")\n'
        'root.joinpath(".pico").mkdir(exist_ok=True)\n'
        'root.joinpath(".pico/private.txt").write_text("runtime-private")\n'
        'root.joinpath(".env").write_text("PRIVATE=do-not-publish")\n'
        'root.joinpath("notes.txt").write_text("new tracked artifact")\n',
        encoding="utf-8",
    )
    verify = tmp_path / "verify.py"
    verify.write_text(
        "import os,runpy,sys\nfrom pathlib import Path\n"
        'assert "RUNTIME_DATABASE_URL" not in os.environ\n'
        'assert "RUNTIME_API_TOKEN" not in os.environ\n'
        'assert "PICO_DEEPSEEK_API_KEY" not in os.environ\n'
        'assert not (Path(sys.argv[1])/".env").exists()\n'
        'assert not (Path(sys.argv[1])/".pico").exists()\n'
        'assert (Path(sys.argv[1])/"notes.txt").is_file()\n'
        'add=runpy.run_path(str(Path(sys.argv[1])/"calculator.py"))["add"]\n'
        'assert add(2,3)==5 and add(-2,3)==1\nprint("verification passed")\n',
        encoding="utf-8",
    )
    settings = Settings(
        store.engine.url,
        "t" * 32,
        tmp_path / "data",
        {
            "demo": Repository(
                source,
                [sys.executable, str(verify), "{workspace}"],
                [sys.executable, str(agent), "{workspace}"],
            )
        },
        lease_seconds=6,
        task_timeout=15,
        verify_timeout=15,
    )
    settings.data_dir.mkdir()
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    job = store.submit(
        "job", {"repository_id": "demo", "task": "Fix addition"}, base_commit=base
    )[0]
    return settings, job, count


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


def test_child_environment_excludes_control_plane_credentials(monkeypatch):
    monkeypatch.setenv("RUNTIME_DATABASE_URL", "do-not-pass")
    monkeypatch.setenv("RUNTIME_API_TOKEN", "do-not-pass")
    monkeypatch.setenv("PICO_DEEPSEEK_API_KEY", "model-key")
    assert "RUNTIME_DATABASE_URL" not in child_environment(provider=True)
    assert "RUNTIME_API_TOKEN" not in child_environment(provider=True)
    assert child_environment(provider=True)["PICO_DEEPSEEK_API_KEY"] == "model-key"
    assert "PICO_DEEPSEEK_API_KEY" not in child_environment()

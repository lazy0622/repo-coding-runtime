"""Regressions found during the independent review of 81985c4.

Each test reproduces a concrete failure observed against the reviewed commit.
"""

import ctypes
import os
import subprocess
import sys
import threading
import time

import pytest
from sqlalchemy.exc import OperationalError

from pico.service.worker import Worker


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# --- 1. Cancellation racing a non-verification failure ---------------------


def test_cancel_racing_agent_failure_does_not_crash_worker(store, configured, tmp_path):
    """Cancel lands, then the Agent exits non-zero before the next heartbeat.

    At 81985c4 the generic failure handler tried ``finish(..., "failed")``;
    the store correctly refused with ``CancellationRequested`` but the worker
    only caught ``LeaseLost``, so the exception escaped ``run_one`` and killed
    the worker loop, leaving the job in ``cancelling`` until lease expiry.
    """
    settings, job, _ = configured
    # A long lease makes the next heartbeat (lease / 3) far away, so the
    # worker cannot learn about the cancellation before the Agent fails.
    settings.lease_seconds = 60
    go = tmp_path / "go.txt"
    started = tmp_path / "started.txt"
    agent = tmp_path / "failing_agent.py"
    agent.write_text(
        "import sys, time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('1')\n"
        f"while not Path({str(go)!r}).exists(): time.sleep(0.02)\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    settings.repositories["demo"].agent_command = [sys.executable, str(agent)]

    outcome = {}

    def target():
        try:
            outcome["returned"] = Worker(settings, store).run_one()
        except BaseException as exc:  # noqa: BLE001 - we assert on it
            outcome["raised"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    try:
        assert _wait_for(started.exists)
        assert store.cancel(job["id"]) == "cancelling"
        go.write_text("1")
        thread.join(timeout=20)
        assert not thread.is_alive()
    finally:
        go.write_text("1")
        thread.join(timeout=20)

    assert "raised" not in outcome, f"worker loop crashed: {outcome.get('raised')!r}"
    assert outcome["returned"] is True
    final = store.get(job["id"])
    assert final["state"] == "cancelled"
    assert final["owner"] is None


# --- 2. Transient heartbeat failure ----------------------------------------


def test_single_transient_heartbeat_error_does_not_abandon_attempt(
    store, configured, tmp_path
):
    """One failed renewal while the lease is still valid must not kill the Agent.

    At 81985c4 any heartbeat exception set ``lost`` immediately: the healthy
    Agent was killed, nothing was published, and the job sat in ``running``
    until expiry, then re-ran the model in generation 2 (extra cost, one of
    three attempts consumed by a single dropped connection).
    """
    settings, job, count = configured
    settings.lease_seconds = 3  # heartbeat every 1s, lease valid for 3s
    agent = tmp_path / "slow_agent.py"
    agent.write_text(
        "import sys, time\nfrom pathlib import Path\n"
        f"Path({str(count)!r}).open('a').write('executed\\n')\n"
        "time.sleep(2.5)\n"
        "Path(sys.argv[1], 'calculator.py').write_text("
        "'def add(a,b):\\n    return a+b\\n')\n"
        "Path(sys.argv[1], 'notes.txt').write_text('n')\n",
        encoding="utf-8",
    )
    settings.repositories["demo"].agent_command = [
        sys.executable,
        str(agent),
        "{workspace}",
    ]
    original = store.heartbeat
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, Exception("connection reset"))
        return original(*args, **kwargs)

    store.heartbeat = flaky
    try:
        assert Worker(settings, store).run_one() is True
    finally:
        store.heartbeat = original

    final = store.get(job["id"])
    assert calls["n"] >= 2
    assert final["state"] == "succeeded", final
    assert final["generation"] == 1
    assert count.read_text().count("executed") == 1


def test_heartbeat_gives_up_once_local_lease_deadline_passes(
    store, configured, tmp_path
):
    """Persistent renewal failure must still stop the Agent before the lease ends."""
    settings, job, _ = configured
    settings.lease_seconds = 3
    sleeper = tmp_path / "sleep_agent.py"
    sleeper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    settings.repositories["demo"].agent_command = [sys.executable, str(sleeper)]

    def always_down(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("database down"))

    original = store.heartbeat
    store.heartbeat = always_down
    started = time.monotonic()
    try:
        assert Worker(settings, store).run_one() is True
    finally:
        store.heartbeat = original
    elapsed = time.monotonic() - started
    # Stopped by the local deadline (< lease), not by the 15s task timeout.
    assert elapsed < settings.lease_seconds + 2, elapsed
    final = store.get(job["id"])
    # Nothing was published: a replacement worker must take over after expiry.
    assert final["state"] == "running" and final["generation"] == 1


def test_worker_loop_survives_database_outage_during_claim():
    """At 81985c4 an OperationalError from ``claim`` ended the worker process."""
    from pico.service.worker import serve

    class DownStore:
        calls = 0

        def claim(self, *args, **kwargs):
            DownStore.calls += 1
            raise OperationalError("SELECT", {}, Exception("server closed"))

    from types import SimpleNamespace

    worker = Worker(SimpleNamespace(lease_seconds=6), DownStore(), worker_id="w")
    assert serve(worker, once=True) is False
    assert DownStore.calls == 1


def test_worker_loop_retries_then_claims_after_database_recovers(monkeypatch):
    from pico.service.worker import serve

    class Finished(BaseException):
        pass

    class RecoveringWorker:
        worker_id = "recovering"
        calls = 0

        def run_one(self):
            self.calls += 1
            if self.calls <= 2:
                raise OperationalError("SELECT", {}, Exception("temporarily down"))
            if self.calls == 3:
                return True
            raise Finished()

    delays = []
    monkeypatch.setattr(time, "sleep", delays.append)
    worker = RecoveringWorker()
    with pytest.raises(Finished):
        serve(worker, idle_seconds=0.1)
    assert worker.calls == 4
    assert delays == [0.1, 0.2]


# --- Confirmed but intentionally not fixed in this review branch ----------


def test_noop_agent_is_explicitly_reported_as_no_change(store, configured, tmp_path):
    settings, job, _ = configured
    noop = tmp_path / "noop.py"
    noop.write_text("pass\n", encoding="utf-8")
    settings.repositories["demo"].agent_command = [sys.executable, str(noop)]
    settings.repositories["demo"].verify_command = [sys.executable, "-c", "pass"]
    Worker(settings, store).run_one()
    final = store.get(job["id"])
    assert final["state"] == "succeeded"
    assert final["result"]["verification_passed"] is True
    assert final["result"]["change_kind"] == "no_change"


def test_temporary_verification_worktree_is_removed_and_agent_evidence_retained(
    store, configured
):
    settings, job, _ = configured
    Worker(settings, store).run_one()
    assert store.get(job["id"])["state"] == "succeeded"
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=settings.repositories["demo"].path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listing.count("worktree ") == 2, listing  # source + retained Agent evidence
    assert "verification-workspace" not in listing
    assert (settings.data_dir / job["id"] / "1/workspace/.pico").is_dir()


# --- 3. Process lifetime on Windows ----------------------------------------


def _alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _force_kill(pid):
    if _alive(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


windows_only = pytest.mark.skipif(
    os.name != "nt", reason="Job Object lifetime binding is Windows-specific"
)


@windows_only
def test_job_assignment_failure_reaps_unassigned_process(tmp_path, monkeypatch):
    from pico.service import worker as module

    created = []
    original = subprocess.Popen

    def remember(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        return process

    def reject_assignment(self, process):
        raise OSError("injected job assignment failure")

    monkeypatch.setattr(module._JobObject, "assign", reject_assignment)
    monkeypatch.setattr(subprocess, "Popen", remember)
    never = threading.Event()
    try:
        with pytest.raises(OSError, match="injected job assignment failure"):
            Worker(None, None)._run(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                tmp_path,
                tmp_path / "assignment-failure.log",
                60,
                never,
                never,
            )
        assert created[0].poll() is not None
    finally:
        for process in created:
            if process.poll() is None:
                _force_kill(process.pid)


@windows_only
def test_hard_killed_worker_does_not_leave_orphan_agent(tmp_path):
    """``taskkill /F`` on the worker (no /T) must also end the Agent process.

    At 81985c4 the Agent was started in a new process group with no lifetime
    binding, so it kept running (and kept calling the model) after its worker
    died; the handoff lists this as an open boundary.
    """
    pid_file = tmp_path / "agent.pid"
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    harness = tmp_path / "harness.py"
    harness.write_text(
        "import sys, threading\nfrom pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "from pico.service.worker import Worker\n"
        "w = Worker(SimpleNamespace(), None, worker_id='harness')\n"
        "never = threading.Event()\n"
        f"w._run([sys.executable, {str(agent)!r}], {str(tmp_path)!r}, "
        f"Path({str(tmp_path / 'agent.log')!r}), 60, never, never)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    worker = subprocess.Popen([sys.executable, str(harness)], env=env)
    agent_pid = None
    try:
        assert _wait_for(pid_file.exists, 20), "agent never started"
        agent_pid = int(pid_file.read_text())
        assert _alive(agent_pid)
        subprocess.run(
            ["taskkill", "/PID", str(worker.pid), "/F"], capture_output=True, check=True
        )
        worker.wait(timeout=10)
        assert _wait_for(lambda: not _alive(agent_pid), 5), "orphan Agent survived"
    finally:
        if agent_pid:
            _force_kill(agent_pid)
        if worker.poll() is None:
            worker.kill()


@windows_only
def test_grandchild_is_reaped_after_agent_root_exits(tmp_path):
    """A background process started by the Agent must not outlive the attempt.

    At 81985c4 ``kill_process_tree`` returned early when the root had already
    exited, so ``taskkill /T`` never ran and the grandchild was left behind.
    """
    pid_file = tmp_path / "grandchild.pid"
    sleeper = tmp_path / "grandchild.py"
    sleeper.write_text(
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    agent = tmp_path / "spawner.py"
    agent.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        # DETACHED_PROCESS: a background job the Agent "forgot" about.
        f"subprocess.Popen([sys.executable, {str(sleeper)!r}], creationflags=0x8)\n"
        f"while not Path({str(pid_file)!r}).exists(): time.sleep(0.02)\n",
        encoding="utf-8",
    )
    worker = Worker(None, None, worker_id="direct")
    never = threading.Event()
    grandchild = None
    try:
        code = worker._run(
            [sys.executable, str(agent)], tmp_path, tmp_path / "a.log", 30, never, never
        )
        assert code == 0
        grandchild = int(pid_file.read_text())
        assert _wait_for(lambda: not _alive(grandchild), 5), "grandchild survived"
    finally:
        if grandchild:
            _force_kill(grandchild)

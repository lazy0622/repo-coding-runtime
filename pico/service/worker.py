"""One task at a time per worker, with heartbeats and fenced publication."""

import argparse
import hashlib
import json
import os
import signal
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from pico.security import redact_text

from .config import Settings
from .store import CancellationRequested, LeaseLost, Store


class Cancelled(RuntimeError):
    pass


class LeaseClock:
    """Conservative local view of the database lease.

    The deadline is measured from *before* each successful claim/renewal
    request was sent, so it can only under-estimate the lease the database
    granted. It lets the worker tolerate a transient renewal error while the
    last confirmed lease is still valid, and stop before that lease can end.
    Publication is still fenced by the database; this only bounds how long a
    worker attempts to keep executing without a confirmed lease. Scheduling
    pauses and process termination latency do not provide a hard real-time bound.
    """

    def __init__(self, lease_seconds, started):
        self.lease_seconds = lease_seconds
        self.deadline = started + lease_seconds

    def renewed(self, sent):
        self.deadline = sent + self.lease_seconds

    def expired(self, now=None):
        return (time.monotonic() if now is None else now) >= self.deadline


class _JobObject:
    """Windows Job Object that terminates every member process when closed.

    Binding Agent/verification processes to a kill-on-close job ties their
    lifetime to the worker: if the worker is hard-killed, the OS closes the
    handle and ends the whole tree, including detached grandchildren.
    """

    _KILL_ON_JOB_CLOSE = 0x2000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._ctypes, self._k32 = ctypes, k32
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
            self.handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process):
        # Popen keeps a full-access process handle in ``_handle``. A process
        # spawned before assignment is not covered. This is a known startup
        # race; this process-lifetime guard is not a security sandbox.
        if not self._k32.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def terminate(self):
        if self.handle:
            self._k32.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


def git(path, *args):
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def kill_process_tree(process, job=None):
    # Descendants may outlive the root (e.g. a background server the Agent
    # started), so do not skip cleanup just because the root has exited.
    if os.name == "nt":
        if job is not None:
            job.terminate()
        elif process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    process.wait(timeout=15)


def child_environment(*, provider=False):
    names = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "LANG",
        "LC_ALL",
        "COMSPEC",
        "PATHEXT",
    )
    env = {key: os.environ[key] for key in names if key in os.environ}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    if provider:
        for name in ("PICO_DEEPSEEK_API_KEY", "PICO_DEEPSEEK_API_BASE"):
            if name in os.environ:
                env[name] = os.environ[name]
    return env


class Worker:
    def __init__(self, settings, store, *, worker_id=None):
        self.settings, self.store = settings, store
        self.worker_id = worker_id or "worker-" + uuid4().hex[:12]

    def _run(
        self, command, cwd, output, timeout, lost, cancel, *, provider=False, lease=None
    ):
        flags = (
            {"start_new_session": True}
            if os.name != "nt"
            else {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            }
        )
        job = _JobObject() if os.name == "nt" else None
        try:
            with output.open("wb") as log:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=child_environment(provider=provider),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    **flags,
                )
                started = time.monotonic()
                job_assigned = False
                try:
                    if job is not None:
                        job.assign(process)
                        job_assigned = True
                    while process.poll() is None:
                        if lost.is_set() or (lease is not None and lease.expired()):
                            raise LeaseLost("Lease lost during execution")
                        if cancel.is_set():
                            raise Cancelled("Cancellation requested")
                        if time.monotonic() - started > timeout:
                            raise TimeoutError("Execution budget exceeded")
                        time.sleep(0.1)
                    return process.returncode
                finally:
                    # A created but unassigned Job cannot terminate this process.
                    # On assignment failure, clean up the actual process tree.
                    kill_process_tree(process, job if job_assigned else None)
        finally:
            if job is not None:
                job.close()

    def _artifact(self, relative):
        path = (self.settings.data_dir / relative).resolve()
        path.relative_to(self.settings.data_dir)
        return path

    def _publish_quietly(self, identity, state, result):
        """Publish a terminal state from an error path without crashing the loop."""
        try:
            self.store.finish(*identity, state, result)
        except CancellationRequested:
            # Cancellation won the race against this failure. ``_run`` has
            # already reaped the managed process tree, so exit is confirmed.
            self._publish_quietly(
                identity,
                "cancelled",
                {
                    "reason": "user_cancelled",
                    "process_exit_confirmed": True,
                    "superseded_failure": result.get("reason"),
                },
            )
        except LeaseLost:
            # A stale worker must not publish a failed/successful replacement result.
            pass

    def run_one(self):
        claimed_at = time.monotonic()
        job = self.store.claim(self.worker_id, self.settings.lease_seconds)
        if job is None:
            return False
        identity = (job["id"], job["generation"], self.worker_id)
        stop, lost, cancel = threading.Event(), threading.Event(), threading.Event()
        lease = LeaseClock(self.settings.lease_seconds, claimed_at)
        interval = self.settings.lease_seconds / 3

        def heartbeat():
            while not stop.wait(interval):
                sent = time.monotonic()
                try:
                    if self.store.heartbeat(*identity, self.settings.lease_seconds):
                        cancel.set()
                    lease.renewed(sent)
                except LeaseLost:
                    lost.set()
                    return
                except Exception:
                    # Transient renewal failure (e.g. dropped DB connection).
                    # Keep going only if the next attempt still happens while
                    # the last confirmed lease is valid; otherwise stop now.
                    if lease.expired(time.monotonic() + interval):
                        lost.set()
                        return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            self._execute(job, identity, lost, cancel, lease)
        except (Cancelled, CancellationRequested):
            self._publish_quietly(
                identity,
                "cancelled",
                {
                    "reason": "user_cancelled",
                    "process_exit_confirmed": True,
                },
            )
        except LeaseLost:
            # A stale worker must not publish a failed/successful replacement result.
            pass
        except Exception as exc:
            self._publish_quietly(
                identity,
                "cancelled" if cancel.is_set() else "failed",
                {
                    "reason": type(exc).__name__,
                    "message": redact_text(str(exc))[:1000],
                },
            )
        finally:
            stop.set()
            thread.join(timeout=5)
            self._cleanup_verification_workspace(job)
        return True

    def _cleanup_verification_workspace(self, job):
        folder = self.settings.data_dir / job["id"] / str(job["generation"])
        # Preserve the Agent's workspace and traces for review. A resumed
        # attempt only reconstructs a verification workspace, so that is temporary.
        workspace = folder / (
            "workspace" if job["checkpoint"] else "verification-workspace"
        )
        if not workspace.exists():
            return
        try:
            workspace.resolve().relative_to(self.settings.data_dir.resolve())
            repository = self.settings.repositories[job["payload"]["repository_id"]]
            git(repository.path, "worktree", "remove", "--force", str(workspace))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            (folder / "cleanup-warning.json").write_text(
                json.dumps(
                    {
                        "error": type(exc).__name__,
                        "message": redact_text(str(exc))[:500],
                    }
                ),
                encoding="utf-8",
            )

    def _execute(self, job, identity, lost, cancel, lease=None):
        repository = self.settings.repositories[job["payload"]["repository_id"]]
        base = job["payload"]["base_commit"]
        folder = self.settings.data_dir / job["id"] / str(job["generation"])
        folder.mkdir(parents=True, exist_ok=False)
        workspace = folder / "workspace"
        git(repository.path, "worktree", "add", "--detach", str(workspace), base)
        self.store.workspace(*identity, str(workspace))
        checkpoint = job["checkpoint"]
        if checkpoint:
            if checkpoint["base_commit"] != base:
                raise ValueError("Checkpoint base commit mismatch")
            patch = self._artifact(checkpoint["patch_path"])
            if hashlib.sha256(patch.read_bytes()).hexdigest() != checkpoint["sha256"]:
                raise ValueError("Checkpoint digest mismatch")
            if patch.stat().st_size:
                git(workspace, "apply", "--binary", str(patch))
        else:
            if repository.agent_command:
                command = [
                    arg.replace("{workspace}", str(workspace))
                    for arg in repository.agent_command
                ]
            else:
                command = [
                    sys.executable,
                    "-m",
                    "pico",
                    "--cwd",
                    str(workspace),
                    "--provider",
                    "deepseek",
                    "--model",
                    "deepseek-flash",
                    "--approval",
                    "auto",
                    "--task-mode",
                    "edit",
                    "--sandbox",
                    repository.sandbox,
                    "--max-steps",
                    "12",
                    "--max-new-tokens",
                    "4096",
                    "--openai-timeout",
                    "90",
                    "--",
                    job["payload"]["task"],
                ]
                if repository.agent_verify_command:
                    development = [
                        arg.replace("{workspace}", str(workspace))
                        for arg in repository.agent_verify_command
                    ]
                    verify_command = (
                        subprocess.list2cmdline(development)
                        if os.name == "nt"
                        else shlex.join(development)
                    )
                    command[-2:-2] = [
                        "--no-plan",
                        "--verify-command",
                        verify_command,
                        "--verify-timeout",
                        str(int(self.settings.verify_timeout)),
                    ]
            code = self._run(
                command,
                workspace,
                folder / "agent.log",
                self.settings.task_timeout,
                lost,
                cancel,
                provider=True,
                lease=lease,
            )
            if code:
                raise RuntimeError(f"Agent exited with code {code}")
            # Stage an explicit literal file list. Negative .pico pathspecs can
            # still make Git fail when the runtime creates an ignored directory.
            git(workspace, "read-tree", "HEAD")
            names = git(
                workspace,
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ).split(b"\0")
            selected = []
            for name in names:
                if not name:
                    continue
                parts = name.decode("utf-8").split("/")
                if ".pico" in parts or any(
                    part == ".env" or part.startswith(".env.") for part in parts
                ):
                    continue
                selected.append(b":(literal)" + name)
            if selected:
                pathspec = folder / "stage-paths.nul"
                pathspec.write_bytes(b"\0".join(selected) + b"\0")
                git(
                    workspace,
                    "add",
                    "-A",
                    "--pathspec-from-file=" + str(pathspec),
                    "--pathspec-file-nul",
                )
            data = git(workspace, "diff", "--cached", "--binary", "HEAD")
            key = os.environ.get("PICO_DEEPSEEK_API_KEY", "")
            if key and key.encode() in data:
                raise ValueError("Patch contains a configured credential")
            patch = folder / "patch.diff"
            with patch.open("xb") as handle:
                handle.write(data)
            checkpoint = {
                "patch_path": str(patch.relative_to(self.settings.data_dir)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "base_commit": base,
                "source_generation": job["generation"],
            }
        self.store.checkpoint(*identity, checkpoint)
        if not job["checkpoint"]:
            # Verify precisely the published patch, excluding private/untracked
            # files that only exist in the Agent's execution workspace.
            workspace = folder / "verification-workspace"
            git(repository.path, "worktree", "add", "--detach", str(workspace), base)
            if patch.stat().st_size:
                git(workspace, "apply", "--binary", str(patch))
        command = [
            arg.replace("{workspace}", str(workspace))
            for arg in repository.verify_command
        ]
        code = self._run(
            command,
            workspace,
            folder / "verification.log",
            self.settings.verify_timeout,
            lost,
            cancel,
            lease=lease,
        )
        result = {
            "change_kind": "patch" if patch.stat().st_size else "no_change",
            "verification_passed": code == 0,
            "verification_exit_code": code,
            "patch_sha256": checkpoint["sha256"],
            "recovery": "resume_verification" if job["checkpoint"] else "new_execution",
            "verification_output": redact_text(
                (folder / "verification.log")
                .read_bytes()[-4000:]
                .decode("utf-8", "replace")
            ),
        }
        self.store.finish(*identity, "succeeded" if code == 0 else "failed", result)


def serve(worker, *, once=False, idle_seconds=0.5, max_backoff=10.0):
    """Worker loop that survives transient database outages.

    An unexpected error (for example ``claim`` failing while PostgreSQL is
    restarting) is logged and retried with backoff instead of ending the
    process; any job left mid-flight is recovered by lease expiry.
    """
    backoff = idle_seconds
    while True:
        try:
            worked = worker.run_one()
            backoff = idle_seconds
        except Exception as exc:  # noqa: BLE001 - loop must stay alive
            worked = False
            print(
                json.dumps(
                    {
                        "worker_id": worker.worker_id,
                        "error": type(exc).__name__,
                        "message": redact_text(str(exc))[:500],
                        "retry_in": backoff,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            if once:
                return False
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
            continue
        if once:
            return worked
        if not worked:
            time.sleep(idle_seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    settings = Settings.load()
    worker = Worker(settings, Store(settings.database_url), worker_id=args.worker_id)
    print(json.dumps({"worker_id": worker.worker_id, "pid": os.getpid()}), flush=True)
    serve(worker, once=args.once)


if __name__ == "__main__":
    main()

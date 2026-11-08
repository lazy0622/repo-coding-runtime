"""One task at a time per worker, with heartbeats and fenced publication."""

import argparse
import hashlib
import json
import os
import signal
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


def git(path, *args):
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def kill_process_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
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

    def _run(self, command, cwd, output, timeout, lost, cancel, *, provider=False):
        flags = (
            {"start_new_session": True}
            if os.name != "nt"
            else {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            }
        )
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
            try:
                while process.poll() is None:
                    if lost.is_set():
                        raise LeaseLost("Lease lost during execution")
                    if cancel.is_set():
                        raise Cancelled("Cancellation requested")
                    if time.monotonic() - started > timeout:
                        raise TimeoutError("Execution budget exceeded")
                    time.sleep(0.1)
                return process.returncode
            finally:
                kill_process_tree(process)

    def _artifact(self, relative):
        path = (self.settings.data_dir / relative).resolve()
        path.relative_to(self.settings.data_dir)
        return path

    def run_one(self):
        job = self.store.claim(self.worker_id, self.settings.lease_seconds)
        if job is None:
            return False
        identity = (job["id"], job["generation"], self.worker_id)
        stop, lost, cancel = threading.Event(), threading.Event(), threading.Event()

        def heartbeat():
            while not stop.wait(self.settings.lease_seconds / 3):
                try:
                    if self.store.heartbeat(*identity, self.settings.lease_seconds):
                        cancel.set()
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            self._execute(job, identity, lost, cancel)
        except (Cancelled, CancellationRequested):
            try:
                self.store.finish(
                    *identity,
                    "cancelled",
                    {
                        "reason": "user_cancelled",
                        "process_exit_confirmed": True,
                    },
                )
            except LeaseLost:
                pass
        except LeaseLost:
            # A stale worker must not publish a failed/successful replacement result.
            pass
        except Exception as exc:
            try:
                state = "cancelled" if cancel.is_set() else "failed"
                self.store.finish(
                    *identity,
                    state,
                    {
                        "reason": type(exc).__name__,
                        "message": redact_text(str(exc))[:1000],
                    },
                )
            except LeaseLost:
                pass
        finally:
            stop.set()
            thread.join(timeout=5)
        return True

    def _execute(self, job, identity, lost, cancel):
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
            code = self._run(
                command,
                workspace,
                folder / "agent.log",
                self.settings.task_timeout,
                lost,
                cancel,
                provider=True,
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
        )
        result = {
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    settings = Settings.load()
    worker = Worker(settings, Store(settings.database_url), worker_id=args.worker_id)
    print(json.dumps({"worker_id": worker.worker_id, "pid": os.getpid()}), flush=True)
    while True:
        worked = worker.run_one()
        if args.once:
            break
        if not worked:
            time.sleep(0.5)


if __name__ == "__main__":
    main()

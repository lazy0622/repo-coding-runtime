"""Execution backends for host and explicit Docker sandbox runs.

The ToolGateway remains the first policy boundary.  This module is the second
boundary for commands that actually execute a process.  Host mode preserves
the historical behaviour; Docker mode creates one short-lived container per
runtime instance and reuses it for all ``run`` calls so a coding task keeps
its process state and installed workspace-local dependencies.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .command_runner import run_shell_command
from .security import is_secret_env_name


SANDBOX_SCHEMA_VERSION = "sandbox-v1"
DEFAULT_SANDBOX_IMAGE = "python:3.11-slim"
DEFAULT_SANDBOX_NETWORK = "none"
DEFAULT_SANDBOX_USER = "65532:65532"
_MEMORY_PATTERN = re.compile(r"^[1-9][0-9]*(?:[kKmMgGtT])?$")


@dataclass(frozen=True)
class SandboxConfig:
    mode: str = "host"
    image: str = DEFAULT_SANDBOX_IMAGE
    network: str = DEFAULT_SANDBOX_NETWORK
    cpus: float = 2.0
    memory: str = "1g"
    pids_limit: int = 128
    user: str = DEFAULT_SANDBOX_USER
    tmpfs_size: str = "256m"

    @classmethod
    def from_value(cls, value=None):
        if isinstance(value, cls):
            return value
        value = dict(value or {})
        config = cls(
            mode=str(value.get("mode", "host")).lower(),
            image=str(value.get("image", DEFAULT_SANDBOX_IMAGE)),
            network=str(value.get("network", DEFAULT_SANDBOX_NETWORK)).lower(),
            cpus=float(value.get("cpus", 2.0)),
            memory=str(value.get("memory", "1g")),
            pids_limit=int(value.get("pids_limit", 128)),
            user=str(value.get("user", DEFAULT_SANDBOX_USER)),
            tmpfs_size=str(value.get("tmpfs_size", "256m")),
        )
        config.validate()
        return config

    def validate(self):
        if self.mode not in {"host", "docker"}:
            raise ValueError("sandbox mode must be host or docker")
        if not self.image.strip():
            raise ValueError("sandbox image must not be empty")
        if self.network not in {"none", "bridge"}:
            raise ValueError("sandbox network must be none or bridge")
        if self.cpus <= 0 or self.cpus > 64:
            raise ValueError("sandbox cpus must be in (0, 64]")
        if not _MEMORY_PATTERN.match(self.memory):
            raise ValueError("sandbox memory must look like 512m or 1g")
        if self.pids_limit < 16 or self.pids_limit > 65536:
            raise ValueError("sandbox pids_limit must be in [16, 65536]")
        if not self.user or self.user in {"0", "0:0", "root"}:
            raise ValueError("sandbox must run as a non-root user")
        if not _MEMORY_PATTERN.match(self.tmpfs_size):
            raise ValueError("sandbox tmpfs_size must look like 256m")
        return self

    def to_dict(self):
        return {
            "schema_version": SANDBOX_SCHEMA_VERSION,
            "mode": self.mode,
            "image": self.image,
            "network": self.network,
            "cpus": self.cpus,
            "memory": self.memory,
            "pids_limit": self.pids_limit,
            "user": self.user,
            "tmpfs_size": self.tmpfs_size,
        }


@dataclass(frozen=True)
class ExecutionResult:
    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    execution_backend: str = "host"
    sandbox_mode: str = "host"
    container_id: str = ""
    timeout_killed: bool = False
    oom_killed: bool = False
    resource_limit_reason: str = ""

    def to_dict(self):
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "execution_backend": self.execution_backend,
            "sandbox_mode": self.sandbox_mode,
            "container_id": self.container_id,
            "timeout_killed": self.timeout_killed,
            "oom_killed": self.oom_killed,
            "resource_limit_reason": self.resource_limit_reason,
        }


class ExecutionBackend:
    """Small lifecycle interface used by shell tools and verification."""

    mode = "host"

    def run(self, command, *, cwd, env, timeout):
        raise NotImplementedError

    def close(self):
        return None

    def report(self):
        return {"schema_version": SANDBOX_SCHEMA_VERSION, "mode": self.mode}


class HostExecutionBackend(ExecutionBackend):
    mode = "host"

    def __init__(self, *, runner=run_shell_command, subprocess_runner=None):
        self.runner = runner
        self.subprocess_runner = subprocess_runner

    def run(self, command, *, cwd, env, timeout):
        started = time.monotonic()
        try:
            kwargs = {
                "cwd": Path(cwd),
                "env": dict(env or {}),
                "timeout": max(1, int(timeout)),
            }
            if self.subprocess_runner is not None:
                kwargs["runner"] = self.subprocess_runner
            result = self.runner(str(command), **kwargs)
            return ExecutionResult(
                command=str(command),
                returncode=int(result.returncode),
                stdout=str(result.stdout or ""),
                stderr=str(result.stderr or ""),
                duration_ms=int((time.monotonic() - started) * 1000),
                execution_backend="host",
                sandbox_mode="host",
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                command=str(command),
                returncode=124,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or "timeout"),
                duration_ms=int((time.monotonic() - started) * 1000),
                execution_backend="host",
                sandbox_mode="host",
                timeout_killed=True,
                resource_limit_reason="timeout",
            )

    def report(self):
        return {
            "schema_version": SANDBOX_SCHEMA_VERSION,
            "mode": "host",
            "execution_backend": "host",
        }


class DockerExecutionBackend(ExecutionBackend):
    """One persistent, explicitly selected Docker container per runtime.

    The backend never falls back to Host.  The caller must choose Host mode
    explicitly if Docker is unavailable.
    """

    mode = "docker"

    def __init__(
        self,
        workspace_root,
        config=None,
        *,
        docker_executable="docker",
        runner=subprocess.run,
    ):
        self.root = Path(workspace_root).resolve()
        self.config = SandboxConfig.from_value({**dict(config.to_dict() if isinstance(config, SandboxConfig) else config or {}), "mode": "docker"})
        self.docker_executable = str(docker_executable)
        self.runner = runner
        self.container_id = ""
        self.container_name = ""
        self._closed = False
        self._env_names: tuple[str, ...] = ()

    def _control(self, args, *, timeout=30, raise_timeout=False):
        command = [self.docker_executable, *[str(item) for item in args]]
        try:
            return self.runner(
                command,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Docker executable was not found; explicit sandbox=docker cannot fall back to host") from exc
        except subprocess.TimeoutExpired as exc:
            if raise_timeout:
                raise
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            return subprocess.CompletedProcess(command, 124, stdout, stderr or "docker control command timed out")

    def preflight(self):
        if shutil.which(self.docker_executable) is None and self.docker_executable == "docker":
            raise RuntimeError("Docker executable was not found; explicit sandbox=docker cannot fall back to host")
        result = self._control(["info"], timeout=20)
        if result.returncode != 0:
            message = str(result.stderr or result.stdout or "Docker daemon is unavailable").strip()
            raise RuntimeError(f"Docker sandbox preflight failed: {message}")
        return {"available": True, "server": str(result.stdout or "").splitlines()[0] if result.stdout else ""}

    def _workspace_path(self, cwd):
        target = Path(cwd).resolve()
        try:
            relative = target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"execution cwd escapes workspace: {target}") from exc
        value = relative.as_posix()
        return "/workspace" if value in {"", "."} else "/workspace/" + value

    @staticmethod
    def _filtered_env(env):
        # The runtime already supplies an allowlisted environment.  This second
        # filter prevents a future caller from accidentally passing obvious
        # secrets into the container.
        return {
            str(name): str(value)
            for name, value in dict(env or {}).items()
            if name
            and name.upper() not in {"PWD", "PATH", "HOME", "USER", "TMP", "TEMP", "TMPDIR"}
            and not is_secret_env_name(name)
        }

    def _create(self, env):
        self.preflight()
        self.container_name = "repo-runtime-" + uuid4().hex[:12]
        filtered = self._filtered_env(env)
        self._env_names = tuple(sorted(filtered))
        args = [
            "create",
            "--name",
            self.container_name,
            "--label",
            "repo-coding-runtime=true",
            "--label",
            f"repo-coding-runtime.name={self.container_name}",
            "--init",
            "--network",
            self.config.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.config.pids_limit),
            "--memory",
            self.config.memory,
            "--cpus",
            str(self.config.cpus),
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={self.config.tmpfs_size}",
            "--volume",
            f"{self.root}:/workspace:rw",
            "--workdir",
            "/workspace",
            "--user",
            self.config.user,
        ]
        for name, value in sorted(filtered.items()):
            if name == "PWD":
                continue
            args.extend(["--env", f"{name}={value}"])
        args.extend([self.config.image, "/bin/sh", "-c", "while :; do sleep 3600; done"])
        result = self._control(args, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(str(result.stderr or result.stdout or "Docker container creation failed").strip())
        self.container_id = str(result.stdout or "").strip().splitlines()[0]
        if not self.container_id:
            raise RuntimeError("Docker container creation returned no container id")
        started = self._control(["start", self.container_id], timeout=30)
        if started.returncode != 0:
            self.close()
            raise RuntimeError(str(started.stderr or started.stdout or "Docker container start failed").strip())

    def _inspect_oom(self):
        if not self.container_id:
            return False
        result = self._control(["inspect", "--format", "{{.State.OOMKilled}}", self.container_id], timeout=10)
        return result.returncode == 0 and str(result.stdout or "").strip().lower() == "true"

    def run(self, command, *, cwd, env, timeout):
        if self._closed:
            raise RuntimeError("Docker execution backend is closed")
        if not self.container_id:
            self._create(env)
        workdir = self._workspace_path(cwd)
        started = time.monotonic()
        exec_args = ["exec", "--workdir", workdir]
        exec_args.extend(["--env", f"PWD={workdir}"])
        exec_args.extend([self.container_id, "/bin/sh", "-lc", str(command)])
        try:
            result = self._control(
                exec_args,
                timeout=max(1, int(timeout)),
                raise_timeout=True,
            )
            oom = int(result.returncode) in {137, 143} or self._inspect_oom()
            return ExecutionResult(
                command=str(command),
                returncode=int(result.returncode),
                stdout=str(result.stdout or ""),
                stderr=str(result.stderr or ""),
                duration_ms=int((time.monotonic() - started) * 1000),
                execution_backend="docker",
                sandbox_mode="docker",
                container_id=self.container_id,
                oom_killed=oom,
                resource_limit_reason="oom_or_sigkill" if oom else "",
            )
        except subprocess.TimeoutExpired:
            container_id = self.container_id
            oom = self._inspect_oom()
            self._control(["stop", "--time", "1", container_id], timeout=10)
            # A timed-out command terminates the persistent session.  Remove
            # the stopped container now so a later command can safely create
            # a fresh session and the timeout path cannot leak resources.
            self._control(["rm", "-f", container_id], timeout=20)
            self.container_id = ""
            return ExecutionResult(
                command=str(command),
                returncode=124,
                stderr="Docker command timed out and the sandbox session was stopped",
                duration_ms=int((time.monotonic() - started) * 1000),
                execution_backend="docker",
                sandbox_mode="docker",
                container_id=container_id,
                timeout_killed=True,
                oom_killed=oom,
                resource_limit_reason="oom" if oom else "timeout",
            )

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.container_id:
            self._control(["rm", "-f", self.container_id], timeout=20)
            self.container_id = ""

    def report(self):
        return {
            "schema_version": SANDBOX_SCHEMA_VERSION,
            "mode": "docker",
            "execution_backend": "docker",
            "container_id": self.container_id,
            "container_name": self.container_name,
            "env_names": list(self._env_names),
            **self.config.to_dict(),
        }


def build_execution_backend(workspace_root, config=None):
    config = SandboxConfig.from_value(config)
    if config.mode == "docker":
        return DockerExecutionBackend(workspace_root, config)
    return HostExecutionBackend()

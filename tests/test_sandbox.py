import subprocess

import pytest

from pico.sandbox import (
    DockerExecutionBackend,
    HostExecutionBackend,
    SandboxConfig,
)


def test_sandbox_config_requires_non_root_and_safe_defaults():
    config = SandboxConfig.from_value()
    assert config.mode == "host"
    assert config.network == "none"
    assert config.user != "0:0"

    with pytest.raises(ValueError, match="non-root"):
        SandboxConfig.from_value({"mode": "docker", "user": "0:0"})


def test_host_backend_keeps_injectable_runner_and_reports_host():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "ok", "")

    result = HostExecutionBackend(runner=runner).run(
        "echo ok",
        cwd=".",
        env={"PATH": "safe"},
        timeout=3,
    )

    assert result.returncode == 0
    assert result.execution_backend == "host"
    assert calls[0][1]["timeout"] == 3


def test_docker_backend_builds_isolated_session_and_filters_secrets(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, "Docker 27", "")
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, "container-123\n", "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, "false\n", "")
        return subprocess.CompletedProcess(command, 0, "output\n", "")

    backend = DockerExecutionBackend(
        tmp_path,
        {"mode": "docker", "image": "repo-runtime:test"},
        docker_executable="fake-docker",
        runner=runner,
    )
    result = backend.run(
        "python -V",
        cwd=tmp_path,
        env={"SAFE_FLAG": "1", "API_KEY": "should-not-enter", "PWD": str(tmp_path)},
        timeout=10,
    )
    backend.close()

    create = next(command for command in calls if command[1] == "create")
    assert result.returncode == 0
    assert result.sandbox_mode == "docker"
    assert "--network" in create and create[create.index("--network") + 1] == "none"
    assert "--cap-drop" in create and create[create.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in create
    assert "--user" in create and create[create.index("--user") + 1] != "0:0"
    assert any("SAFE_FLAG=1" == value for value in create)
    assert not any("API_KEY" in value for value in create)
    assert not any(value.startswith("PATH=") for value in create)
    assert any(command[1] == "rm" for command in calls)


def test_docker_backend_timeout_stops_session_without_host_fallback(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0, "Docker", "")
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, "container-timeout\n", "")
        if command[1] == "exec":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, "false\n", "")

    backend = DockerExecutionBackend(
        tmp_path,
        {"mode": "docker"},
        docker_executable="fake-docker",
        runner=runner,
    )
    result = backend.run("sleep 100", cwd=tmp_path, env={}, timeout=1)

    assert result.returncode == 124
    assert result.timeout_killed is True
    assert result.resource_limit_reason == "timeout"
    assert any(command[1] == "stop" for command in calls)

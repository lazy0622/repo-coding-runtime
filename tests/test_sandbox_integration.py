import os
import shutil

import pytest

from pico.sandbox import DockerExecutionBackend


pytestmark = pytest.mark.docker


def test_real_docker_sandbox_executes_non_root_command(tmp_path):
    if os.environ.get("PICO_DOCKER_INTEGRATION") != "1":
        pytest.skip("set PICO_DOCKER_INTEGRATION=1 to run Docker integration tests")
    if shutil.which("docker") is None:
        pytest.skip("Docker executable unavailable")

    backend = DockerExecutionBackend(
        tmp_path,
        {"mode": "docker", "image": os.environ.get("PICO_SANDBOX_IMAGE", "python:3.11-slim")},
    )
    try:
        result = backend.run(
            "python -c \"import os; print(os.getuid())\"",
            cwd=tmp_path,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            timeout=30,
        )
        assert result.returncode == 0
        assert result.sandbox_mode == "docker"
        assert result.stdout.strip() not in {"", "0"}
    finally:
        backend.close()

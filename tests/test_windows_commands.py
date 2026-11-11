import os
import sys

import pytest

from pico.command_runner import run_shell_command

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows shell regression")


@pytest.mark.parametrize("prefix", ["", "cd src && "])
@pytest.mark.parametrize("source", ["print('body executed')", "print(17)"])
def test_multiline_inline_python_executes_body_and_preserves_exit_code(
    tmp_path, prefix, source
):
    (tmp_path / "src").mkdir()
    command = prefix + f'python -c "\nimport sys\n{source}\nsys.exit(7)\n"'
    result = run_shell_command(command, cwd=tmp_path, env=os.environ.copy(), timeout=10)
    assert result.returncode == 7
    assert result.stdout.strip() in {"body executed", "17"}


def test_inline_python_cd_uses_requested_directory(tmp_path):
    (tmp_path / "source folder").mkdir()
    command = 'cd "source folder" && python -c "\nfrom pathlib import Path\nprint(Path.cwd().name)\n"'
    result = run_shell_command(command, cwd=tmp_path, env=os.environ.copy(), timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == "source folder"


def test_following_shell_commands_keep_shell_semantics(tmp_path):
    command = f'"{sys.executable}" -c "print(1)" && echo next'
    result = run_shell_command(command, cwd=tmp_path, env=os.environ.copy(), timeout=10)
    assert result.returncode == 0
    assert "next" in result.stdout


def test_unsupported_multiline_chain_does_not_report_false_success(tmp_path):
    with pytest.raises(ValueError, match="Multiline Windows shell"):
        run_shell_command("echo first\necho second", cwd=tmp_path, env={}, timeout=10)


def test_non_utf8_output_does_not_lose_the_real_exit_code(tmp_path):
    result = run_shell_command(
        'python -c "import sys;sys.stdout.buffer.write(bytes([255]));sys.exit(7)"',
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=10,
    )
    assert result.returncode == 7 and result.stdout


def test_runtime_verifier_can_import_windows_asyncio_without_passing_credentials(
    tmp_path, monkeypatch
):
    from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext

    monkeypatch.setenv("PICO_DEEPSEEK_API_KEY", "must-not-inherit")
    agent = Pico(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico/sessions"),
    )
    env = agent.shell_env()
    assert env.get("SystemRoot") and "PICO_DEEPSEEK_API_KEY" not in env
    result = run_shell_command(
        'python -c "import asyncio;print(17)"',
        cwd=tmp_path,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout.strip() == "17"

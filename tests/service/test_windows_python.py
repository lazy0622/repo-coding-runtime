import os
import sys

import pytest

from pico.command_runner import _portable_python_argv


@pytest.mark.skipif(os.name != "nt", reason="Native Windows launcher compatibility")
def test_windows_store_python3_alias_uses_runtime_interpreter(monkeypatch):
    monkeypatch.setattr(
        "pico.command_runner.shutil.which",
        lambda _: r"C:\Users\demo\AppData\Local\Microsoft\WindowsApps\python3.exe",
    )
    assert _portable_python_argv("python3 -c 'print(1)'")[0] == sys.executable


@pytest.mark.skipif(os.name != "nt", reason="Native Windows launcher compatibility")
def test_real_python3_installation_is_preserved(monkeypatch):
    monkeypatch.setattr(
        "pico.command_runner.shutil.which", lambda _: r"C:\Python\python3.exe"
    )
    assert _portable_python_argv("python3 -c 'print(1)'")[0] == "python3"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows launcher compatibility")
def test_python_command_chain_keeps_existing_shell_boundary():
    assert _portable_python_argv("python3 -c 'print(1)' && echo next") is None

"""Cross-platform command execution helpers.

The runtime still accepts a human-authored shell command because that is the
most useful CLI contract.  On Windows, however, benchmark and test commands
are often written with POSIX-style quoting (for example ``python -c '...'``).
This module detects the narrow, safe-to-normalize Python ``-c`` shape and
executes it without a shell.  Other commands keep the existing shell
semantics and continue to pass through the normal security policy first.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable


_PYTHON_NAMES = {"python", "python3", "python.exe", "python3.exe", "py", "py.exe"}
_SHELL_OPERATORS = {"&", "&&", "|", "||", ";", "<", ">"}
CommandRunner = Callable[..., subprocess.CompletedProcess]


def _looks_like_python(value: str) -> bool:
    name = Path(value).name.lower()
    return name in _PYTHON_NAMES or name.endswith("python.exe") or name.endswith("python3.exe")


def _portable_python_argv(command: str) -> list[str] | None:
    """Return argv for a simple Python ``-c`` command on Windows.

    This intentionally handles only Python invocations.  It does not try to
    parse a general shell language, so pipes, redirects and command chains
    remain on the normal shell path.
    """

    if os.name != "nt":
        return None

    text = str(command or "").strip()
    if not text or "'" not in text:
        return None

    # An unquoted absolute Windows executable path contains backslashes that
    # POSIX shlex would remove.  Keep it on cmd.exe's normal path; quoted
    # single-quote forms are the compatibility case this helper is meant to
    # normalize.
    if len(text) > 3 and text[1] == ":" and text[2] in {"\\", "/"}:
        return None

    # A Windows executable emitted by subprocess.list2cmdline is normally
    # double-quoted and contains backslashes.  Let cmd.exe handle that form;
    # shlex would interpret the backslashes as POSIX escapes.
    if text.startswith('"') and ":\\" in text[:160]:
        return None

    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return None

    if len(argv) < 3 or argv[1] != "-c" or not _looks_like_python(argv[0]):
        return None
    if any(token in _SHELL_OPERATORS for token in argv[1:]):
        return None

    # ``python3`` is common in portable benchmark definitions but is not
    # always registered as a Windows executable.  Use the current interpreter
    # when the requested alias cannot be resolved.
    if argv[0].lower() in {"python3", "python3.exe"} and shutil.which(argv[0]) is None:
        argv[0] = sys.executable
    return argv


def run_shell_command(
    command,
    *,
    cwd,
    env,
    timeout,
    runner: CommandRunner = subprocess.run,
):
    """Run a command while preserving the existing shell contract.

    ``runner`` is injectable so the ToolGateway's focused tests can continue
    to observe the actual process boundary without coupling the runtime to a
    particular subprocess implementation.
    """

    argv = _portable_python_argv(str(command or ""))
    if argv is not None:
        return runner(
            argv,
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    return runner(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def format_python_command(code: str) -> str:
    """Build a portable CLI command for a Python snippet."""

    argv = [sys.executable, "-c", str(code)]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)

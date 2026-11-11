"""Real Pi RPC command contracts, no model or provider requests."""

import argparse
import ctypes
import json
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path

from benchmarks.pi_compare.pi_agent import launch


def exited(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no PID
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


def wait_marker(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text())
        time.sleep(0.05)
    raise TimeoutError("Command did not write its PID marker")


def main(args):
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Refusing to overwrite a smoke result")
    previous = os.environ.get("PICO_DEEPSEEK_API_KEY")
    os.environ["PICO_DEEPSEEK_API_KEY"] = "smoke-only-not-a-provider-key"
    rows = []
    python = shlex.quote(sys.executable.replace("\\", "/"))

    def command(source):
        return python + " -c " + shlex.quote(source)

    try:
        with tempfile.TemporaryDirectory(prefix="pi-contract-") as temporary:
            root = Path(temporary)
            (root / "space folder").mkdir()
            rpc = launch(args.node, args.cli, root, root / "first", shell=args.shell)
            try:
                rpc.request("get_state")
                cases = [
                    (
                        "multiline_cwd_and_unicode",
                        "cd 'space folder' && "
                        + command(
                            "import pathlib\nprint(pathlib.Path.cwd().name)\nprint('中文✓')"
                        ),
                        0,
                        ("space folder", "中文✓"),
                    ),
                    (
                        "nonzero_exit",
                        command("import sys; print('before-exit'); sys.exit(7)"),
                        7,
                        ("before-exit",),
                    ),
                    (
                        "windows_asyncio_prerequisites",
                        command("import asyncio; print('asyncio-ok')"),
                        0,
                        ("asyncio-ok",),
                    ),
                    (
                        "builtin_format",
                        command("print(format(1234567, ','))"),
                        0,
                        ("1,234,567",),
                    ),
                    (
                        "model_key_not_in_command_environment",
                        command(
                            "import os; assert 'PICO_DEEPSEEK_API_KEY' not in os.environ; "
                            "assert 'RUNTIME_DATABASE_URL' not in os.environ; "
                            "assert 'RUNTIME_API_TOKEN' not in os.environ; print('credentials-absent')"
                        ),
                        0,
                        ("credentials-absent",),
                    ),
                    (
                        "non_utf8_output_does_not_crash",
                        command(
                            "import sys; sys.stdout.buffer.write(b'raw-\\xff-done')"
                        ),
                        0,
                        ("raw-", "-done"),
                    ),
                ]
                for name, shell, code, needles in cases:
                    result = rpc.request("bash", command=shell)
                    passed = result.get("exitCode") == code and all(
                        x in result.get("output", "") for x in needles
                    )
                    rows.append(
                        {
                            "case": name,
                            "passed": passed,
                            "exit_code": result.get("exitCode"),
                            "output": result.get("output", ""),
                        }
                    )
                marker = root / "cancel.pid"
                rpc.send(
                    {
                        "id": "sleep",
                        "type": "bash",
                        "command": command(
                            f"from pathlib import Path; import os,time; Path({marker.as_posix()!r}).write_text(str(os.getpid())); time.sleep(60)"
                        ),
                    }
                )
                pid = wait_marker(marker)
                rpc.request("abort_bash")
                result = rpc.wait_response("sleep")
                deadline = time.monotonic() + 5
                while not exited(pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                rows.append(
                    {
                        "case": "rpc_cancel_reaps_child",
                        "passed": result.get("cancelled") is True and exited(pid),
                    }
                )
            finally:
                rpc.close()
            timed = launch(
                args.node, args.cli, root, root / "deadline", shell=args.shell
            )
            marker = root / "timeout.pid"
            try:
                timed.request("get_state")
                timed.send(
                    {
                        "id": "deadline",
                        "type": "bash",
                        "command": command(
                            f"from pathlib import Path; import os,time; Path({marker.as_posix()!r}).write_text(str(os.getpid())); time.sleep(60)"
                        ),
                    }
                )
                pid = wait_marker(marker)
                did_timeout = False
                try:
                    while True:
                        timed.receive(time.monotonic() + 0.2)
                except TimeoutError:
                    did_timeout = True
            finally:
                timed.close()
            deadline = time.monotonic() + 5
            while not exited(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            rows.append(
                {
                    "case": "host_deadline_reaps_child",
                    "passed": did_timeout and exited(pid),
                }
            )
    finally:
        if previous is None:
            os.environ.pop("PICO_DEEPSEEK_API_KEY", None)
        else:
            os.environ["PICO_DEEPSEEK_API_KEY"] = previous
        report = {
            "tier": "real_pi_rpc_contracts_no_model_calls",
            "engine_version": "1.5.1",
            "model_calls": 0,
            "passed": sum(x["passed"] for x in rows),
            "observed": len(rows),
            "cases": rows,
        }
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps({key: report[key] for key in ("passed", "observed", "model_calls")})
    )
    return 0 if len(rows) == 8 and all(x["passed"] for x in rows) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--shell", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(main(parser.parse_args()))

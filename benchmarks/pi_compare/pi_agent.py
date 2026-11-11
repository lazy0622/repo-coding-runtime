"""Experimental Pi RPC execution adapter; the service still owns acceptance."""

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

from pico.security import redact_text
from pico.service.worker import _JobObject, child_environment, kill_process_tree

EXTENSION = Path(__file__).with_name("service_extension.ts")


class PiRPC:
    """One isolated subprocess with LF-delimited UTF-8 RPC and bounded waits."""

    def __init__(self, command, *, cwd, env, stderr):
        self.records = queue.Queue()
        self.responses = {}
        self.stderr = stderr.open("wb")
        self.job = _JobObject() if os.name == "nt" else None
        self.process = None
        self.assigned = False
        try:
            flags = (
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {"start_new_session": True}
            )
            self.process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                **flags,
            )
            if self.job:
                self.job.assign(self.process)
                self.assigned = True
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            # Binary readline splits only LF, not valid JSON U+2028/U+2029.
            for line in self.process.stdout:
                self.records.put(json.loads(line.decode("utf-8")))
        except Exception as exc:
            self.records.put(exc)
        finally:
            self.records.put(EOFError("Pi RPC stdout closed"))

    def send(self, command):
        self.process.stdin.write(
            json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
        )
        self.process.stdin.flush()

    def receive(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Pi RPC deadline exceeded")
        try:
            record = self.records.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("Pi RPC deadline exceeded") from exc
        if isinstance(record, BaseException):
            raise record
        if record.get("type") == "extension_error":
            raise RuntimeError(
                "Pi extension failed: " + str(record.get("error", "unknown"))
            )
        return record

    def request(self, kind, *, timeout=20, **fields):
        request_id = uuid4().hex
        self.send({"id": request_id, "type": kind, **fields})
        return self.wait_response(request_id, timeout=timeout)

    def wait_response(self, request_id, *, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            event = self.responses.pop(request_id, None) or self.receive(deadline)
            if event.get("type") == "response":
                if event.get("id") != request_id:
                    self.responses[event.get("id")] = event
                    continue
                if not event.get("success"):
                    raise RuntimeError(event.get("error", "Pi command failed"))
                return event.get("data") or {}

    def close(self):
        if self.process:
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                kill_process_tree(self.process, self.job if self.assigned else None)
                self.process.wait(timeout=10)
            # Also reap background children after a normally exiting root.
            kill_process_tree(self.process, self.job if self.assigned else None)
            if self.process.stdout:
                self.process.stdout.close()
        if self.job:
            self.job.close()
        self.stderr.close()


def launch(node, cli, workspace, folder, *, model=True, shell=None):
    config = folder / "pi-config"
    config.mkdir(parents=True, exist_ok=True)
    base = os.environ.get(
        "PICO_DEEPSEEK_API_BASE", "https://api.deepseek.com/anthropic"
    ).rstrip("/")
    # Anthropic SDK appends /v1/messages; Pico's direct HTTP client appends
    # /messages to a normalized /v1 base instead.
    base = base.removesuffix("/v1")
    (config / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "runtime-deepseek": {
                        "api": "anthropic-messages",
                        "baseUrl": base,
                        "apiKey": "$PICO_DEEPSEEK_API_KEY",
                        "models": [
                            {
                                "id": "deepseek-flash",
                                "reasoning": False,
                                "maxTokens": 4096,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = {
        "cacheWarming": "off",
        "retry": {"enabled": False, "provider": {"maxRetries": 0}},
    }
    if shell:
        settings["shellPath"] = str(shell)
    (config / "settings.json").write_text(
        json.dumps(settings),
        encoding="utf-8",
    )
    env = child_environment(provider=model)
    env["PI_CODING_AGENT_DIR"] = str(config)
    env["PI_SKIP_VERSION_CHECK"] = "1"
    env["PI_TELEMETRY"] = "0"
    if shell:
        env["RUNTIME_PI_BASH"] = str(shell)
    command = [
        str(node),
        str(cli),
        "--mode",
        "rpc",
        "--offline",
        "--no-session",
        "--no-extensions",
        "--no-mcp",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--tools",
        "read,bash,edit,write,grep,find,ls",
        "-e",
        str(EXTENSION),
    ]
    if model:
        command += [
            "--provider",
            "runtime-deepseek",
            "--model",
            "deepseek-flash",
            "--thinking",
            "off",
        ]
    return PiRPC(command, cwd=workspace, env=env, stderr=folder / "pi-stderr.log")


def run(args):
    workspace = args.workspace.resolve(strict=True)
    folder = workspace / ".pico" / "runs" / ("pi-" + uuid4().hex)
    folder.mkdir(parents=True)
    rpc = None
    report = {
        "engine": "pi",
        "engine_version": "1.5.1",
        "status": "failed",
        "stop_reason": "adapter_error",
        "tool_steps": 0,
        "model_calls": 0,
        "thinking": False,
        "max_model_calls": 13,
    }
    started = time.monotonic()
    last = None
    try:
        rpc = launch(args.node, args.cli, workspace, folder, shell=args.shell)
        rpc.request("set_auto_retry", enabled=False)
        rpc.send({"id": "task", "type": "prompt", "message": args.task})
        deadline = started + args.timeout
        with (folder / "trace.jsonl").open("w", encoding="utf-8") as trace:
            while True:
                event = rpc.receive(deadline)
                kind = event.get("type")
                if kind == "response" and not event.get("success"):
                    raise RuntimeError(event.get("error", "Pi request rejected"))
                if (
                    kind == "message_start"
                    and event.get("message", {}).get("role") == "assistant"
                ):
                    if report["model_calls"] >= 13:
                        report["stop_reason"] = "model_call_limit_reached"
                        raise RuntimeError("Pi model-call budget exhausted")
                if kind == "tool_execution_start":
                    report["tool_steps"] += 1
                    trace.write(
                        json.dumps(
                            {"event": "pi_tool_start", "tool": event.get("toolName")}
                        )
                        + "\n"
                    )
                if (
                    kind == "message_end"
                    and event.get("message", {}).get("role") == "assistant"
                ):
                    last = event["message"]
                    usage = last.get("usage") or {}
                    report["model_calls"] += 1
                    trace.write(
                        json.dumps(
                            {
                                "event": "model_parsed",
                                "engine": "pi",
                                "completion_metadata": {
                                    "input_tokens": sum(
                                        usage.get(key, 0) or 0
                                        for key in ("input", "cacheRead", "cacheWrite")
                                    ),
                                    "output_tokens": usage.get("output", 0) or 0,
                                },
                            }
                        )
                        + "\n"
                    )
                    trace.flush()
                if kind == "agent_settled":
                    if (
                        event.get("aborted")
                        or not last
                        or last.get("stopReason") != "stop"
                    ):
                        raise RuntimeError(
                            (last or {}).get("errorMessage")
                            or "Pi settled without a successful final assistant message"
                        )
                    report.update(
                        status="completed", stop_reason="final_answer_returned"
                    )
                    break
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, TimeoutError):
            report["stop_reason"] = "timeout"
        message = redact_text(str(exc))
        key = os.environ.get("PICO_DEEPSEEK_API_KEY")
        if key:
            message = message.replace(key, "[REDACTED]")
        report["error_message"] = message[:1000]
    finally:
        if rpc:
            try:
                rpc.close()
            except Exception as exc:
                report.update(
                    status="failed",
                    stop_reason="process_cleanup_failed",
                    error_type=type(exc).__name__,
                )
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (folder / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--shell", type=Path)
    parser.add_argument("task", help="Literal task text; place after --")
    parser.add_argument("--timeout", type=float, default=230)
    raise SystemExit(run(parser.parse_args()))

"""Minimal MCP stdio client and Tool Registry provider.

MCP tools are registered into Pico's normal registry, so they cannot bypass the
same validation, approval, tracing, and workspace policy used by built-ins.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .tool_registry import ToolSpec


DEFAULT_PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    pass


class MCPClient(Protocol):
    def list_tools(self): ...

    def call_tool(self, name, arguments): ...

    def close(self): ...


class MCPProvider(Protocol):
    def register(self, registry): ...

    def close(self): ...


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_").lower()


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: tuple[str, ...]
    cwd: str = ""
    env: dict = field(default_factory=dict)
    timeout: float = 20.0
    protocol_version: str = DEFAULT_PROTOCOL_VERSION

    @classmethod
    def from_mapping(cls, name, value):
        value = dict(value or {})
        command = value.get("command")
        args = value.get("args", []) or []
        if isinstance(command, list):
            command_parts = [str(part) for part in command]
        elif command:
            command_parts = [str(command)]
        else:
            raise ValueError(f"MCP server {name} is missing command")
        command_parts.extend(str(part) for part in args)
        return cls(
            name=str(name),
            command=tuple(command_parts),
            cwd=str(value.get("cwd", "")),
            env={str(key): str(item) for key, item in dict(value.get("env", {}) or {}).items()},
            timeout=float(value.get("timeout", 20.0)),
            protocol_version=str(value.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)),
        )


def load_mcp_server_configs(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    servers = document.get("mcpServers", document)
    if not isinstance(servers, dict):
        raise ValueError("MCP config must contain an mcpServers object")
    return [MCPServerConfig.from_mapping(name, value) for name, value in servers.items()]


class MCPStdioClient:
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process = None
        self._responses = queue.Queue()
        self._stderr_lines = []
        self._request_id = 0
        self._initialized = False
        self._write_lock = threading.Lock()

    def _environment(self):
        allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG")
        environment = {name: os.environ[name] for name in allowed if name in os.environ}
        environment.update(self.config.env)
        return environment

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.process = subprocess.Popen(
            list(self.config.command),
            cwd=self.config.cwd or None,
            env=self._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        atexit.register(self.close)

    def _read_stdout(self):
        if self.process is None or self.process.stdout is None:
            return
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                self._stderr_lines.append(f"invalid-json:{line[:200]}")

    def _read_stderr(self):
        if self.process is None or self.process.stderr is None:
            return
        for line in self.process.stderr:
            line = line.rstrip()
            if line:
                self._stderr_lines.append(line[-500:])
                del self._stderr_lines[:-20]

    def _send(self, message):
        self.start()
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise MCPError(f"MCP server {self.config.name} is not running")
        with self._write_lock:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def _request(self, method, params=None):
        self._request_id += 1
        request_id = self._request_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": str(method), "params": dict(params or {})})
        while True:
            try:
                message = self._responses.get(timeout=self.config.timeout)
            except queue.Empty as exc:
                detail = " | ".join(self._stderr_lines[-3:])
                raise MCPError(f"MCP request timed out: {method}{': ' + detail if detail else ''}") from exc
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise MCPError(f"MCP {method} failed: {message['error']}")
            return dict(message.get("result", {}) or {})

    def initialize(self):
        if self._initialized:
            return
        self._request(
            "initialize",
            {
                "protocolVersion": self.config.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "pico", "version": "0.1.0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self._initialized = True

    def list_tools(self):
        self.initialize()
        tools = []
        cursor = None
        while True:
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []) or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, arguments):
        self.initialize()
        return self._request("tools/call", {"name": str(name), "arguments": dict(arguments or {})})

    def close(self):
        process = self.process
        self.process = None
        self._initialized = False
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _render_mcp_result(result):
    blocks = result.get("content", []) if isinstance(result, dict) else []
    rendered = []
    for block in blocks or []:
        if not isinstance(block, dict):
            rendered.append(str(block))
        elif block.get("type") == "text":
            rendered.append(str(block.get("text", "")))
        else:
            rendered.append(json.dumps(block, ensure_ascii=False, sort_keys=True))
    if not rendered:
        rendered.append(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return "\n".join(item for item in rendered if item).strip() or "(empty)"


class MCPToolProvider:
    def __init__(self, config: MCPServerConfig, client=None, strict=False):
        self.config = config
        self.client = client or MCPStdioClient(config)
        self.strict = bool(strict)
        self.diagnostics = []
        self.tool_names = []

    def register(self, registry):
        try:
            tools = self.client.list_tools()
        except Exception as exc:
            self.diagnostics.append({"server": self.config.name, "level": "error", "message": str(exc)})
            if self.strict:
                raise
            return []

        registered = []
        for tool in tools:
            remote_name = str(tool.get("name", "")).strip()
            if not remote_name:
                continue
            local_name = f"mcp__{_safe_name(self.config.name)}__{_safe_name(remote_name)}"
            input_schema = dict(tool.get("inputSchema", {}) or {})
            annotations = dict(tool.get("annotations", {}) or {})
            read_only = bool(annotations.get("readOnlyHint", False))
            properties = input_schema.get("properties", {}) or {}
            required = set(input_schema.get("required", []) or [])
            compact_schema = {
                name: f"{definition.get('type', 'any')}{'' if name in required else '=optional'}"
                for name, definition in properties.items()
                if isinstance(definition, dict)
            }

            def runner(args, tool_name=remote_name):
                result = self.client.call_tool(tool_name, args)
                if isinstance(result, dict) and result.get("isError"):
                    raise MCPError(_render_mcp_result(result))
                return _render_mcp_result(result)

            try:
                registry.register(
                    ToolSpec(
                    name=local_name,
                    schema=compact_schema,
                    description=str(tool.get("description", "MCP tool")),
                    runner=runner,
                    risky=not read_only,
                    source=f"mcp:{self.config.name}",
                    input_schema=input_schema,
                    metadata={"server": self.config.name, "remote_name": remote_name, "annotations": annotations},
                    )
                )
            except Exception as exc:
                self.diagnostics.append(
                    {"server": self.config.name, "tool": remote_name, "level": "error", "message": str(exc)}
                )
                if self.strict:
                    raise
                continue
            registered.append(local_name)
        self.tool_names = registered
        return registered

    def close(self):
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

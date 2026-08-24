"""Deterministic regression checks for native tool-call compatibility."""

from __future__ import annotations

import json
from pathlib import Path

from ..providers.tool_calls import ModelCompletion, ToolCall, provider_tool_definitions
from ..runtime import Pico


TOOL_PROTOCOL_SCHEMA_VERSION = "tool-protocol-v1"


def _case(name, passed, detail):
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def run_tool_protocol_benchmark(artifact_path=None):
    """Run provider-free protocol checks and optionally write a JSON artifact."""

    native = ModelCompletion(
        "",
        tool_calls=(
            ToolCall(
                name="read_file",
                args={"path": "README.md", "start": 1, "end": 2},
                call_id="call_1",
                protocol="openai_responses",
            ),
        ),
        protocol="openai_responses",
    )
    native_kind, native_payload = Pico.parse(native)
    malformed = ModelCompletion(
        "",
        tool_calls=(
            ToolCall(
                name="read_file",
                args={},
                protocol="anthropic_messages",
                error="tool arguments are not valid JSON",
            ),
        ),
        protocol="anthropic_messages",
    )
    malformed_kind, _ = Pico.parse(malformed)
    multiple = ModelCompletion(
        "",
        tool_calls=(
            ToolCall(name="read_file", args={}, protocol="openai_responses"),
            ToolCall(name="list_files", args={}, protocol="openai_responses"),
        ),
        protocol="openai_responses",
    )
    multiple_kind, _ = Pico.parse(multiple)
    xml_kind, xml_payload = Pico.parse(
        '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>'
    )

    tools = {
        "read_file": {
            "schema": {"path": "str", "start": "int=1"},
            "description": "Read a file.",
            "risky": False,
        }
    }
    openai_schema = provider_tool_definitions(tools, "openai_responses")
    anthropic_schema = provider_tool_definitions(tools, "anthropic_messages")
    cases = [
        _case(
            "native_call_parse",
            native_kind == "tool" and native_payload["_tool_call_id"] == "call_1",
            "structured ToolCall reaches the runtime parser",
        ),
        _case(
            "native_argument_error_rejected",
            malformed_kind == "retry",
            "malformed provider arguments do not reach ToolGateway",
        ),
        _case(
            "multiple_native_calls_rejected",
            multiple_kind == "retry",
            "the one-tool-per-turn safety contract is enforced",
        ),
        _case(
            "xml_fallback_parse",
            xml_kind == "tool" and xml_payload["name"] == "read_file",
            "text-only XML clients keep the legacy path",
        ),
        _case(
            "openai_schema_conversion",
            openai_schema[0]["type"] == "function" and "parameters" in openai_schema[0],
            "OpenAI Responses function schema",
        ),
        _case(
            "anthropic_schema_conversion",
            "input_schema" in anthropic_schema[0] and "type" not in anthropic_schema[0],
            "Anthropic Messages tool schema",
        ),
    ]
    passed = sum(1 for item in cases if item["passed"])
    native_cases = cases[:3]
    artifact = {
        "artifact_type": "tool-protocol-benchmark",
        "schema_version": TOOL_PROTOCOL_SCHEMA_VERSION,
        "provider": "scripted-no-network",
        "summary": {
            "total_cases": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "pass_rate": passed / len(cases) if cases else 0.0,
        },
        "metrics": {
            "native_call_contract_rate": sum(item["passed"] for item in native_cases) / len(native_cases),
            "xml_fallback_contract_rate": 1.0 if cases[3]["passed"] else 0.0,
            "schema_conversion_rate": sum(item["passed"] for item in cases[4:]) / 2,
            "network_calls": 0,
        },
        "cases": cases,
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def render_tool_protocol_markdown(artifact):
    summary = artifact["summary"]
    metrics = artifact["metrics"]
    lines = [
        "# Native Tool Protocol Benchmark",
        "",
        "Deterministic provider-free contract checks; no API key or network call.",
        "",
        f"- Passed: `{summary['passed']}/{summary['total_cases']}`",
        f"- Native contract rate: `{metrics['native_call_contract_rate']:.2f}`",
        f"- XML fallback contract rate: `{metrics['xml_fallback_contract_rate']:.2f}`",
        f"- Schema conversion rate: `{metrics['schema_conversion_rate']:.2f}`",
        "",
        "| Case | Status | Detail |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| `{item['name']}` | {'passed' if item['passed'] else 'failed'} | {item['detail']} |"
        for item in artifact["cases"]
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "TOOL_PROTOCOL_SCHEMA_VERSION",
    "run_tool_protocol_benchmark",
    "render_tool_protocol_markdown",
]

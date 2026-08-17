"""Provider-neutral tool-call objects and provider tool schemas.

The runtime historically used a small XML protocol because it works with
local and text-only models.  Native provider tool calls are represented here
without making the control loop depend on OpenAI or Anthropic response types.
"""

from __future__ import annotations

import ast
import copy
import json
from dataclasses import dataclass
from typing import Any, Mapping


_TYPE_NAMES = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "array",
    "array": "array",
    "dict": "object",
    "object": "object",
}


@dataclass(frozen=True)
class ToolCall:
    """A normalized model-requested tool invocation."""

    name: str
    args: dict[str, Any]
    call_id: str = ""
    protocol: str = "native"
    raw_arguments: str = ""
    error: str = ""

    def to_runtime_payload(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "args": dict(self.args),
        }
        if self.call_id:
            payload["_tool_call_id"] = self.call_id
        if self.protocol:
            payload["_tool_protocol"] = self.protocol
        if self.error:
            payload["_tool_call_error"] = self.error
        return payload


class ModelCompletion(str):
    """String-compatible completion with optional structured tool calls.

    Existing FakeModelClient/Ollama tests and XML fallbacks continue to see a
    normal string.  Native providers attach structured calls to this string
    subclass so the current ``Pico.parse`` API can evolve without breaking
    callers that compare completions with text.
    """

    def __new__(
        cls,
        text: str = "",
        *,
        tool_calls=(),
        protocol: str = "text",
        response_id: str = "",
        stop_reason: str = "",
    ):
        value = super().__new__(cls, str(text or ""))
        value.tool_calls = tuple(tool_calls or ())
        value.protocol = str(protocol or "text")
        value.response_id = str(response_id or "")
        value.stop_reason = str(stop_reason or "")
        return value

    def metadata(self) -> dict[str, Any]:
        names = [call.name for call in self.tool_calls if call.name]
        result = {
            "tool_protocol": self.protocol,
            "native_tool_call_count": len(self.tool_calls),
            "native_tool_names": names,
        }
        if self.response_id:
            result["provider_response_id"] = self.response_id
        if self.stop_reason:
            result["provider_stop_reason"] = self.stop_reason
        return result


def parse_tool_arguments(arguments: Any) -> tuple[dict[str, Any], str]:
    """Decode provider arguments while retaining a useful runtime error."""

    if arguments is None:
        return {}, ""
    if isinstance(arguments, Mapping):
        return dict(arguments), ""
    raw = str(arguments)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {}, f"tool arguments are not valid JSON: {exc}"
    if not isinstance(value, dict):
        return {}, "tool arguments must decode to a JSON object"
    return value, ""


def make_tool_call(
    name: Any,
    arguments: Any,
    *,
    call_id: Any = "",
    protocol: str,
) -> ToolCall:
    args, error = parse_tool_arguments(arguments)
    return ToolCall(
        name=str(name or "").strip(),
        args=args,
        call_id=str(call_id or ""),
        protocol=str(protocol),
        raw_arguments=str(arguments) if arguments is not None and not isinstance(arguments, Mapping) else "",
        error=error,
    )


def _literal_default(value: str):
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None


def _legacy_field_schema(value: Any) -> tuple[dict[str, Any], bool]:
    """Convert the project's compact ``str='.'`` schema notation."""

    declaration = str(value or "").strip()
    # ``when resuming`` is documentation, not a type expression.
    declaration = declaration.split(" when ", 1)[0].strip()
    has_default = "=" in declaration
    type_text, default_text = declaration.split("=", 1) if has_default else (declaration, "")
    json_type = _TYPE_NAMES.get(type_text.strip().lower(), "string")
    field_schema: dict[str, Any] = {"type": json_type}
    if has_default:
        default = _literal_default(default_text.strip())
        if default is not None:
            field_schema["default"] = default
    return field_schema, not has_default


def canonical_input_schema(tool: Mapping[str, Any]) -> dict[str, Any]:
    """Return a provider-neutral JSON Schema for a registered tool."""

    explicit = tool.get("input_schema") or {}
    if isinstance(explicit, Mapping) and explicit.get("type") == "object":
        return copy.deepcopy(dict(explicit))

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, declaration in dict(tool.get("schema", {}) or {}).items():
        field_schema, is_required = _legacy_field_schema(declaration)
        properties[str(name)] = field_schema
        if is_required:
            required.append(str(name))
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def provider_tool_definitions(tools, protocol: str) -> list[dict[str, Any]]:
    """Convert the active registry to one provider's native tool format."""

    definitions = []
    for name in sorted(tools):
        tool = tools[name]
        schema = canonical_input_schema(tool)
        description = str(tool.get("description", "")).strip()
        if protocol == "openai_responses":
            definitions.append(
                {
                    "type": "function",
                    "name": str(name),
                    "description": description,
                    "parameters": schema,
                    # The runtime still validates arguments.  Keeping strict
                    # off preserves optional/default fields in old tool specs.
                    "strict": False,
                }
            )
        elif protocol == "anthropic_messages":
            definitions.append(
                {
                    "name": str(name),
                    "description": description,
                    "input_schema": schema,
                }
            )
        else:
            raise ValueError(f"unsupported native tool protocol: {protocol}")
    return definitions

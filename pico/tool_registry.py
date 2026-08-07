"""Extensible tool registry shared by built-ins, MCP providers, and tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Callable
import hashlib
import json


ToolRunner = Callable[[dict], str]
ToolValidator = Callable[[dict], None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: dict
    description: str
    runner: ToolRunner
    risky: bool = False
    validator: ToolValidator | None = None
    example: str = ""
    source: str = "builtin"
    input_schema: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any]):
        return cls(
            name=str(name),
            schema=dict(value.get("schema", {})),
            description=str(value.get("description", "")),
            runner=value["run"],
            risky=bool(value.get("risky", False)),
            validator=value.get("validate") or value.get("validator"),
            example=str(value.get("example", "")),
            source=str(value.get("source", "builtin")),
            input_schema=dict(value.get("input_schema", {}) or {}),
            metadata=dict(value.get("metadata", {}) or {}),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": dict(self.schema),
            "risky": self.risky,
            "description": self.description,
            "run": self.runner,
            "validate": self.validator,
            "example": self.example,
            "source": self.source,
            "input_schema": dict(self.input_schema),
            "metadata": dict(self.metadata),
        }


class ToolRegistry(Mapping):
    """Mapping-compatible registry so legacy ``agent.tools`` callers keep working."""

    def __init__(self, specs=()):
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    @classmethod
    def from_mapping(cls, tools: Mapping[str, Mapping[str, Any]]):
        return cls(ToolSpec.from_mapping(name, value) for name, value in tools.items())

    def register(self, spec: ToolSpec, *, replace=False):
        if not isinstance(spec, ToolSpec):
            raise TypeError("tool spec must be a ToolSpec")
        if not spec.name.strip():
            raise ValueError("tool name must not be empty")
        if spec.name in self._specs and not replace:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        return spec

    def register_many(self, specs, *, replace=False):
        return [self.register(spec, replace=replace) for spec in specs]

    def spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(str(name))

    def validate(self, name: str, args):
        spec = self.spec(name)
        if spec is None:
            raise ValueError(f"unknown tool: {name}")
        args = args or {}
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        if spec.validator is not None:
            spec.validator(args)
            return
        if spec.input_schema:
            validate_json_schema_arguments(spec.input_schema, args)

    def __getitem__(self, name):
        return self._specs[name].to_mapping()

    def __iter__(self) -> Iterator[str]:
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def signature(self):
        payload = []
        for name in sorted(self._specs):
            spec = self._specs[name]
            payload.append(
                {
                    "name": spec.name,
                    "schema": spec.schema,
                    "description": spec.description,
                    "risky": spec.risky,
                    "source": spec.source,
                    "input_schema": spec.input_schema,
                    "metadata": spec.metadata,
                }
            )
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def validate_json_schema_arguments(schema, args):
    """Validate the small JSON-Schema subset needed by MCP tool declarations."""

    if not isinstance(schema, dict):
        return
    required = schema.get("required", []) or []
    for name in required:
        if name not in args:
            raise ValueError(f"missing required argument: {name}")

    properties = schema.get("properties", {}) or {}
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for name, value in args.items():
        definition = properties.get(name)
        if not isinstance(definition, dict):
            continue
        expected_name = definition.get("type")
        expected = type_map.get(expected_name)
        if expected is None:
            continue
        if expected_name in {"integer", "number"} and isinstance(value, bool):
            raise ValueError(f"argument {name} must be {expected_name}")
        if not isinstance(value, expected):
            raise ValueError(f"argument {name} must be {expected_name}")

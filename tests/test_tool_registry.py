import pytest

from pico.tool_registry import ToolRegistry, ToolSpec


def test_tool_registry_is_mapping_compatible_and_runs_registered_tool():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            schema={"text": "string"},
            description="Echo text.",
            runner=lambda args: args["text"],
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        )
    )

    registry.validate("echo", {"text": "hello"})

    assert list(registry) == ["echo"]
    assert registry["echo"]["run"]({"text": "hello"}) == "hello"
    assert registry.spec("echo").source == "builtin"


@pytest.mark.parametrize("args", [{}, {"text": 42}])
def test_tool_registry_rejects_invalid_json_schema_arguments(args):
    registry = ToolRegistry(
        [
            ToolSpec(
                name="echo",
                schema={},
                description="Echo text.",
                runner=lambda values: values["text"],
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ]
    )

    with pytest.raises(ValueError):
        registry.validate("echo", args)

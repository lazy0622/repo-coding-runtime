import json
from unittest.mock import patch

from pico import FakeModelClient, ModelCompletion, Pico, SessionStore, ToolCall, WorkspaceContext
from pico.providers.clients import AnthropicCompatibleModelClient, OpenAICompatibleModelClient
from pico.providers.tool_calls import provider_tool_definitions
from pico.tools import build_tool_registry


class _Context:
    depth = 0
    max_depth = 1

    def __init__(self, root):
        self.root = root


def _tool_registry(tmp_path):
    return build_tool_registry(_Context(tmp_path))


def test_provider_tool_definitions_use_provider_specific_shapes(tmp_path):
    tools = _tool_registry(tmp_path)

    openai = provider_tool_definitions(tools, "openai_responses")
    anthropic = provider_tool_definitions(tools, "anthropic_messages")

    read_openai = next(item for item in openai if item["name"] == "read_file")
    read_anthropic = next(item for item in anthropic if item["name"] == "read_file")
    assert read_openai["type"] == "function"
    assert read_openai["parameters"]["properties"]["start"] == {"type": "integer", "default": 1}
    assert "path" in read_openai["parameters"]["required"]
    assert "type" not in read_anthropic
    assert read_anthropic["input_schema"]["type"] == "object"
    assert read_anthropic["input_schema"]["properties"]["end"]["default"] == 200


def test_openai_responses_extracts_native_function_call_and_sends_tools():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "id": "resp_native_1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "read_file",
                            "arguments": '{"path":"README.md","start":1,"end":2}',
                        }
                    ],
                }
            ).encode("utf-8")

    client = OpenAICompatibleModelClient(
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        temperature=0.0,
        timeout=30,
    )
    tools = [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}]

    def fake_urlopen(request, timeout):
        del timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("inspect", 128, tools=tools)

    assert isinstance(result, ModelCompletion)
    assert result == ""
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].args == {"path": "README.md", "start": 1, "end": 2}
    assert result.tool_calls[0].call_id == "call_1"
    assert captured["body"]["tools"] == tools
    assert captured["body"]["parallel_tool_calls"] is False
    assert client.last_completion_metadata["native_tool_call_count"] == 1


def test_anthropic_messages_extracts_tool_use_and_sends_tools():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "id": "msg_native_1",
                    "stop_reason": "tool_use",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_file",
                            "input": {"path": "README.md", "start": 1, "end": 2},
                        }
                    ],
                }
            ).encode("utf-8")

    client = AnthropicCompatibleModelClient(
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        temperature=0.0,
        timeout=30,
    )
    tools = [{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}]

    def fake_urlopen(request, timeout):
        del timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        result = client.complete("inspect", 128, tools=tools)

    assert isinstance(result, ModelCompletion)
    assert result.tool_calls[0].protocol == "anthropic_messages"
    assert result.tool_calls[0].call_id == "toolu_1"
    assert captured["body"]["tools"] == tools
    assert client.last_completion_metadata["provider_stop_reason"] == "tool_use"


def test_openai_native_continuation_uses_function_call_output():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"output_text": "<final>done</final>"}).encode("utf-8")

    client = OpenAICompatibleModelClient(
        model="gpt-test",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        temperature=0.0,
        timeout=30,
    )
    context = {
        "protocol": "openai_responses",
        "call_id": "call_1",
        "name": "read_file",
        "args": {"path": "README.md"},
        "result": "demo",
        "assistant_prompt": "inspect",
    }

    def fake_urlopen(request, timeout):
        del timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        client.complete("continue", 128, tools=[], native_tool_result=context)

    assert captured["body"]["input"][1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read_file",
        "arguments": '{"path": "README.md"}',
    }
    assert captured["body"]["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "demo",
    }


def test_anthropic_native_continuation_uses_tool_result_block():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"content": [{"type": "text", "text": "<final>done</final>"}]}).encode("utf-8")

    client = AnthropicCompatibleModelClient(
        model="claude-test",
        base_url="https://api.anthropic.com/v1",
        api_key="sk-test",
        temperature=0.0,
        timeout=30,
    )
    context = {
        "protocol": "anthropic_messages",
        "call_id": "toolu_1",
        "name": "read_file",
        "args": {"path": "README.md"},
        "result": "demo",
        "assistant_prompt": "inspect",
    }

    def fake_urlopen(request, timeout):
        del timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        client.complete("continue", 128, tools=[], native_tool_result=context)

    assert captured["body"]["messages"][1]["content"][0]["type"] == "tool_use"
    assert captured["body"]["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "content": "demo",
    }


def test_runtime_prefers_native_tool_call_and_keeps_xml_fallback(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    native = ModelCompletion(
        "",
        tool_calls=(
            ToolCall(
                name="read_file",
                args={"path": "README.md", "start": 1, "end": 1},
                call_id="call_1",
                protocol="openai_responses",
            ),
        ),
        protocol="openai_responses",
    )
    kind, payload = Pico.parse(native)
    assert kind == "tool"
    assert payload["name"] == "read_file"
    assert payload["args"]["path"] == "README.md"
    assert payload["_tool_call_id"] == "call_1"

    assert Pico.parse('<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>')[0] == "tool"


def test_agent_loop_passes_native_tools_and_executes_call(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")

    class NativeFakeClient(FakeModelClient):
        supports_native_tool_calling = True
        native_tool_protocol = "openai_responses"

        def __init__(self):
            super().__init__([])
            self.supports_native_tool_calling = True
            self.native_tool_protocol = "openai_responses"
            self.received_tools = []
            self.received_contexts = []
            self.turn = 0

        def complete(self, prompt, max_new_tokens, **kwargs):
            del prompt, max_new_tokens
            self.received_tools.append(kwargs.get("tools"))
            self.received_contexts.append(kwargs.get("native_tool_result"))
            self.turn += 1
            if self.turn == 1:
                return ModelCompletion(
                    "",
                    tool_calls=(
                        ToolCall(
                            name="read_file",
                            args={"path": "README.md", "start": 1, "end": 1},
                            call_id="call_1",
                            protocol="openai_responses",
                        ),
                    ),
                    protocol="openai_responses",
                )
            return "<final>Done.</final>"

    client = NativeFakeClient()
    agent = Pico(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("Inspect README.md") == "Done."
    assert client.received_tools and client.received_tools[0]
    assert any(item["name"] == "read_file" for item in client.received_tools[0])
    assert client.received_contexts[0] is None
    assert client.received_contexts[1]["call_id"] == "call_1"
    assert "native function tools" in agent.prefix

import sys

from pico import FakeModelClient, Pico, SessionStore, WorkspaceContext
from pico.mcp import MCPServerConfig, MCPStdioClient, MCPToolProvider


class FakeMCPClient:
    def __init__(self, tools, result=None):
        self.tools = tools
        self.result = result or {"content": [{"type": "text", "text": "remote result"}]}
        self.calls = []
        self.closed = False

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result

    def close(self):
        self.closed = True


def build_agent(tmp_path, provider):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return Pico(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".pico" / "sessions"),
        approval_policy="auto",
        tool_providers=[provider],
    )


def test_mcp_tools_register_and_execute_through_normal_gateway(tmp_path):
    client = FakeMCPClient(
        [
            {
                "name": "search",
                "description": "Search a remote index.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "annotations": {"readOnlyHint": True},
            }
        ]
    )
    provider = MCPToolProvider(MCPServerConfig(name="docs", command=("fake",)), client=client)
    agent = build_agent(tmp_path, provider)

    result = agent.execute_tool("mcp__docs__search", {"query": "pico"})

    assert result.content == "remote result"
    assert result.metadata["read_only"] is True
    assert client.calls == [("search", {"query": "pico"})]
    assert agent.tool_registry.spec("mcp__docs__search").source == "mcp:docs"
    agent.close()
    assert client.closed is True


def test_mcp_json_schema_validation_rejects_call_before_remote_execution(tmp_path):
    client = FakeMCPClient(
        [
            {
                "name": "search",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                "annotations": {"readOnlyHint": True},
            }
        ]
    )
    provider = MCPToolProvider(MCPServerConfig(name="docs", command=("fake",)), client=client)
    agent = build_agent(tmp_path, provider)

    result = agent.execute_tool("mcp__docs__search", {})

    assert result.metadata["tool_status"] == "rejected"
    assert result.metadata["tool_error_code"] == "invalid_arguments"
    assert client.calls == []


def test_mcp_error_result_is_mapped_to_gateway_failure(tmp_path):
    client = FakeMCPClient(
        [
            {
                "name": "lookup",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True},
            }
        ],
        result={"isError": True, "content": [{"type": "text", "text": "remote failure"}]},
    )
    provider = MCPToolProvider(MCPServerConfig(name="docs", command=("fake",)), client=client)
    agent = build_agent(tmp_path, provider)

    result = agent.execute_tool("mcp__docs__lookup", {})

    assert result.metadata["tool_status"] == "error"
    assert result.metadata["tool_error_code"] == "tool_failed"
    assert "remote failure" in result.content


def test_mcp_stdio_client_runs_initialize_list_call_and_close(tmp_path):
    server = tmp_path / "fake_mcp_server.py"
    server.write_text(
        """import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    request_id = message.get("id")
    if request_id is None:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": message["params"]["arguments"]["text"]}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    client = MCPStdioClient(
        MCPServerConfig(name="fake", command=(sys.executable, str(server)), timeout=5)
    )

    tools = client.list_tools()
    result = client.call_tool("echo", {"text": "hello"})
    client.close()

    assert tools[0]["name"] == "echo"
    assert result["content"][0]["text"] == "hello"
    assert client.process is None

"""Model provider adapters."""

from .clients import AnthropicCompatibleModelClient, FakeModelClient, OllamaModelClient, OpenAICompatibleModelClient
from .tool_calls import ModelCompletion, ToolCall, canonical_input_schema, provider_tool_definitions

__all__ = [
    "AnthropicCompatibleModelClient",
    "FakeModelClient",
    "OllamaModelClient",
    "OpenAICompatibleModelClient",
    "ModelCompletion",
    "ToolCall",
    "canonical_input_schema",
    "provider_tool_definitions",
]

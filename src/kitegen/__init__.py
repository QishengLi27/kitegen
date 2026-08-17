"""kitegen — A lightweight agent framework.

Composable agents, tasks, crews, and graphs. Bring your own LLM and tools.

Usage:
    import kitegen as kg

    @kg.tool
    def search(query: str) -> str:
        return "..."

    agent = kg.Agent(
        role="researcher",
        goal="Find facts",
        tools=[search],
        llm=kg.OpenAIAdapter(model="gpt-4o"),
    )

    result = await agent.run({"topic": "AI"})
"""

from kitegen.agent import Agent
from kitegen.checkpoint import Checkpointer, MemorySaver, PostgresSaver
from kitegen.deploy import to_worker
from kitegen.memory import BufferMemory, Memory
from kitegen.core import (
    Complete,
    Context,
    Custom,
    Executable,
    FunctionExecutable,
    Interrupt,
    InterruptError,
    KitegenError,
    LLMRetryableError,
    NodeEnd,
    NodeError,
    NodeStart,
    NodeTrace,
    RetryPolicy,
    Runnable,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    as_executable,
    execute_with_retry,
    interrupt,
)
from kitegen.graph import Graph, stream_event
from kitegen.llm import (
    AnthropicAdapter,
    LLM,
    LLMAdapter,
    LLMResponse,
    LiteLLMAdapter,
    Message,
    OpenAIAdapter,
    ToolCall,
    Usage,
)
from kitegen.resilience import CircuitBreaker, CircuitOpenError, TokenTracker
from kitegen.tool import Tool, tool

# Backward compatibility: legacy direct LLM helpers
from kitegen.contrib.llm import ChatResponse, chat, chat_stream, chat_structured

__version__ = "0.1.0"

__all__ = [
    # Core
    "Context",
    "Executable",
    "Runnable",
    "FunctionExecutable",
    "as_executable",
    "execute_with_retry",
    "RetryPolicy",
    "KitegenError",
    "LLMRetryableError",
    "InterruptError",
    "interrupt",
    "NodeTrace",
    "NodeStart",
    "NodeEnd",
    "NodeError",
    "Interrupt",
    "Complete",
    "Custom",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    # Graph
    "Graph",
    "Agent",
    "stream_event",
    # Tools
    "Tool",
    "tool",
    # LLM adapters
    "LLM",
    "LLMAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "LiteLLMAdapter",
    "LLMResponse",
    "Message",
    "ToolCall",
    "Usage",
    # Resilience
    "CircuitBreaker",
    "CircuitOpenError",
    "TokenTracker",
    # Checkpoints
    "Checkpointer",
    "MemorySaver",
    "PostgresSaver",
    # Deployment
    "to_worker",
    # Memory
    "Memory",
    "BufferMemory",
    # Legacy compatibility
    "ChatResponse",
    "chat",
    "chat_stream",
    "chat_structured",
]

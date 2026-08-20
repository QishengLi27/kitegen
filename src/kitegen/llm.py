"""kitegen.llm — Minimal LLM abstraction with adapters.

The framework is intentionally unopinionated about LLM providers. This module
provides a thin protocol (`LLM`) and a few common adapters. Users can bring
any client they want.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol


# ── Pricing (per 1K tokens, USD) ──────────────────────────────────────────

PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat":      {"input": 0.00027, "output": 0.00110},
    "deepseek-reasoner":  {"input": 0.00055, "output": 0.00219},
    # DeepSeek V4 peak/off-peak scheme (2026-08-17, approx USD @7.2 CNY/USD,
    # off-peak rates; peak hours are 2x). Update if you need exact billing.
    "deepseek-v4-flash":  {"input": 0.00021, "output": 0.00063},
    "deepseek-v4-pro":    {"input": 0.00076, "output": 0.00375},
    "gpt-4o":             {"input": 0.00250, "output": 0.01000},
    "gpt-4o-mini":        {"input": 0.00015, "output": 0.00060},
    "gpt-4.1-nano":       {"input": 0.00010, "output": 0.00040},
    "claude-sonnet-4-5":  {"input": 0.00300, "output": 0.01500},
    "claude-haiku-4-5":   {"input": 0.00080, "output": 0.00400},
    "glm-4-flash":        {"input": 0.00010, "output": 0.00010},
}


@dataclass
class Usage:
    """Token usage from an LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_openai(cls, usage: Any) -> Usage:
        """Create Usage from an OpenAI-compatible response.usage object."""
        if usage is None:
            return Usage()
        return cls(
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            total_tokens=getattr(usage, "total_tokens", 0),
        )

    def cost(self, model: str) -> float:
        """Calculate cost in USD for this usage."""
        p = PRICING.get(model)
        if not p:
            return 0.0
        return (self.prompt_tokens / 1000) * p["input"] + \
               (self.completion_tokens / 1000) * p["output"]


@dataclass
class ToolCall:
    """A single tool call requested by an LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Response from an LLM chat completion."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str | None = None


@dataclass
class Message:
    """A single chat message."""

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_openai(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role}
        if self.role == "tool":
            msg["content"] = self.content or ""
            msg["tool_call_id"] = self.tool_call_id
        elif self.tool_calls:
            msg["content"] = self.content or ""
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": (
                            tc.arguments if isinstance(tc.arguments, str) else str(tc.arguments)
                        ),
                    },
                }
                for tc in self.tool_calls
            ]
        else:
            msg["content"] = self.content or ""
        return msg


class LLM(Protocol):
    """Protocol for LLM adapters."""

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        ...


class LLMAdapter(ABC):
    """Base class for LLM adapters."""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        raise NotImplementedError


class OpenAIAdapter(LLMAdapter):
    """Adapter for OpenAI-compatible APIs.

    Model resolution order: explicit `model` argument → ``LLM_MODEL`` env
    var → ``OPENAI_MODEL`` env var → "gpt-4o". This lets deployment
    environments switch models without code changes.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        tracker: Any | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ):
        if model is None:
            import os
            model = (
                os.getenv("LLM_MODEL")
                or os.getenv("OPENAI_MODEL")
                or "gpt-4o"
            )
        self.model = model
        self._client = client
        self._api_key = api_key
        self._base_url = base_url
        # Optional TokenTracker (duck-typed to avoid a circular import —
        # resilience imports Usage from this module). Every chat() call
        # records its usage into it.
        self.tracker = tracker
        # Temperature can be pinned per-model via LLM_TEMPERATURE. Some
        # providers (e.g. Kimi k3-256k) only accept temperature=1.
        if temperature is None:
            import os
            env_temp = os.getenv("LLM_TEMPERATURE")
            temperature = float(env_temp) if env_temp is not None else 0.0
        self.temperature = temperature
        # Timeout for the underlying HTTP client. Slow providers (e.g. Kimi
        # k3-256k) can easily exceed 30 s on multi-stock analysis, so the
        # default is deliberately generous.
        if timeout is None:
            import os
            env_timeout = os.getenv("LLM_TIMEOUT")
            timeout = float(env_timeout) if env_timeout is not None else 120.0
        self.timeout = timeout

    def _ensure_client(self):
        if self._client is not None:
            return
        import os
        from openai import AsyncOpenAI
        key = self._api_key or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        base = self._base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_API_BASE")
        if key is None:
            raise RuntimeError(
                "No API key found. Set OPENAI_API_KEY or LLM_API_KEY environment variable."
            )
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=base,
            timeout=self.timeout,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._ensure_client()
        model_name = model or self.model
        request: dict[str, Any] = {
            "model": model_name,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"

        response = await self._client.chat.completions.create(**request, **kwargs)
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            import json

            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        usage = Usage.from_openai(response.usage)
        if self.tracker is not None:
            self.tracker.record(model_name, usage)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            model=model_name,
        )


class AnthropicAdapter(LLMAdapter):
    """Adapter for Anthropic Claude API.

    Requires `anthropic` to be installed separately.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-3-5-sonnet-latest",
        client: Any | None = None,
    ):
        self.model = model
        if client is not None:
            self._client = client
        else:
            import os
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def _to_anthropic_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert generic messages to Anthropic format."""
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            out.append({"role": m.role, "content": m.content or ""})
        return out

    def _extract_system(self, messages: list[Message]) -> str | None:
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        return "\n".join(system_parts) if system_parts else None

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = 0.0,
        max_tokens: int | None = 1024,
        **kwargs: Any,
    ) -> LLMResponse:
        model_name = model or self.model
        request: dict[str, Any] = {
            "model": model_name,
            "messages": self._to_anthropic_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens or 1024,
        }
        system = self._extract_system(messages)
        if system:
            request["system"] = system
        if tools:
            request["tools"] = tools

        response = await self._client.messages.create(**request, **kwargs)
        content = ""
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )

        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            usage=Usage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            ),
            model=model_name,
        )


class LiteLLMAdapter(LLMAdapter):
    """Adapter using LiteLLM for many providers.

    Requires `litellm` to be installed separately.
    """

    def __init__(self, model: str = "gpt-4o"):
        self.model = model

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = 0.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        import litellm

        model_name = model or self.model
        openai_messages = [m.to_openai() for m in messages]
        response = await litellm.acompletion(
            model=model_name,
            messages=openai_messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            import json

            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=Usage.from_openai(response.usage),
            model=model_name,
        )


__all__ = [
    "Usage",
    "ToolCall",
    "LLMResponse",
    "Message",
    "LLM",
    "LLMAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "LiteLLMAdapter",
]

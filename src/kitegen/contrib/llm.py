"""kitegen.contrib.llm — Legacy convenience helpers for direct LLM calls.

This module is kept for backward compatibility. It is not part of the core
framework. New code should use the LLM adapter interface in `kitegen.llm`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kitegen.llm import OpenAIAdapter, Usage
from kitegen.resilience import TokenTracker


@dataclass
class ChatResponse:
    content: str
    usage: Usage
    model: str


async def _get_client():
    import os
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=os.getenv("LLM_API_KEY", "sk-placeholder"),
        base_url=os.getenv("LLM_API_BASE", "https://api.openai.com/v1"),
    )


async def chat(
    system_prompt: str,
    user_message: str,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    tracker: TokenTracker | None = None,
    json_mode: bool = False,
) -> ChatResponse:
    """Send a chat request. Returns ChatResponse with content + usage."""
    client = await _get_client()

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(**kwargs)

    usage = Usage.from_openai(response.usage) if response.usage else Usage()
    if tracker:
        tracker.record(model, usage)

    return ChatResponse(
        content=response.choices[0].message.content or "",
        usage=usage,
        model=model,
    )


async def chat_stream(
    system_prompt: str,
    user_message: str,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    tracker: TokenTracker | None = None,
):
    """Stream chat response tokens as they arrive."""
    client = await _get_client()

    response = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        stream=True,
        stream_options={"include_usage": True},
    )

    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content
        if chunk.usage:
            usage = Usage.from_openai(chunk.usage)
            if tracker:
                tracker.record(model, usage)


async def chat_structured(
    system_prompt: str,
    user_message: str,
    output_schema: type,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    tracker: TokenTracker | None = None,
) -> Any:
    """Chat with structured output parsing via Pydantic."""
    client = await _get_client()

    try:
        response = await client.beta.chat.completions.parse(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format=output_schema,
        )
        usage = Usage.from_openai(response.usage) if response.usage else Usage()
        if tracker:
            tracker.record(model, usage)
        parsed = response.choices[0].message.parsed
        if parsed is not None:
            return parsed
    except Exception:
        pass

    resp = await chat(
        system_prompt=system_prompt + "\n\nRespond with ONLY a valid JSON object.",
        user_message=user_message,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        tracker=tracker,
        json_mode=True,
    )

    raw = resp.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]

    return output_schema.model_validate_json(raw)


__all__ = ["ChatResponse", "chat", "chat_stream", "chat_structured"]

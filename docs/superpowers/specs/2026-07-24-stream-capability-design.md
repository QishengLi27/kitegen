# Stream Capability for kitegen

**Date:** 2026-07-24  
**Status:** Design approved  

## Overview

Add streaming at two levels: LLM (token-by-token output from `chat_stream()`) and Agent (graph execution events from `invoke_stream()`). Both follow kitegen's minimal philosophy — plain data, no magic.

## LLM-level: `chat_stream()`

### API

```python
from kitegen.llm import chat_stream

async for chunk in chat_stream(
    "You are helpful", "Tell me a story",
    model="deepseek-chat", tracker=tracker,
):
    print(chunk, end="")
```

### Signature

```python
async def chat_stream(
    system_prompt: str,
    user_message: str,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    tracker: TokenTracker | None = None,
) -> AsyncIterator[str]:
```

### Behavior

- Returns `AsyncIterator[str]` — each item is a text delta (not raw OpenAI chunks)
- Uses `stream=True` on the OpenAI API call
- If `tracker` is passed, accumulates usage from the final stream chunk (which contains `usage` on OpenAI-compatible APIs)
- Same parameter signature as `chat()` for familiarity
- Does NOT support `json_mode` (structured output streaming is a separate concern; use `chat_structured()` for that)

### Implementation (~25 lines)

Wraps `AsyncOpenAI().chat.completions.create(stream=True)`, iterates chunks, yields `chunk.choices[0].delta.content or ""` for each, and records usage from the last chunk.

## Agent-level: `invoke_stream()` and `stream_event()`

### API

```python
async for event in agent.invoke_stream(state, thread_id="session-1"):
    match event:
        case NodeStart(node=name):
            print(f"[{name}] started")
        case NodeEnd(node=name):
            print(f"[{name}] done")
        case NodeError(node=name, error=msg):
            print(f"[{name}] FAILED: {msg}")
        case Interrupt(node=name, payload=data):
            print(f"[{name}] paused: {data}")
            # save data, later call agent.resume_stream(...)
        case Complete():
            print("Graph finished")
        case Custom(node=name, data=payload):
            # User-defined — e.g., {"type": "token", "content": "hello"}
            if payload.get("type") == "token":
                print(payload["content"], end="")
```

### Event types

```python
@dataclass
class NodeStart:
    node: str

@dataclass
class NodeEnd:
    node: str
    trace: NodeTrace

@dataclass
class NodeError:
    node: str
    error: str

@dataclass
class Interrupt:
    node: str
    payload: Any

@dataclass
class Complete:
    pass

@dataclass
class Custom:
    node: str
    data: Any
```

All events live in `kitegen.graph` and are exported from the top-level package.

### `stream_event()`

A context function (like `interrupt()`) that nodes call to emit custom data into the agent stream:

```python
async def my_node(state):
    async for chunk in chat_stream("You are helpful", state["prompt"]):
        await stream_event({"type": "token", "content": chunk})
    return state
```

Uses a `ContextVar`-based queue. The data is wrapped in a `Custom` event and yielded by `invoke_stream()`.

### `invoke_stream()`

New method on `Agent`:

```python
async def invoke_stream(
    self,
    state: dict[str, Any],
    thread_id: str | None = None,
    max_retries: int = 2,
) -> AsyncIterator[NodeStart | NodeEnd | NodeError | Interrupt | Complete | Custom]:
```

Same execution logic as `invoke()` (checkpoint restore, node loop, conditional edges, retry, interrupt handling), but:
- Yields events at each lifecycle point instead of just returning final state
- On `InterruptError`, yields an `Interrupt` event and returns (caller can `resume_stream()` later)
- Sets up a `ContextVar`-backed event queue that `stream_event()` pushes into

### `resume_stream()`

```python
async def resume_stream(
    self, data: Any, thread_id: str
) -> AsyncIterator[NodeStart | NodeEnd | NodeError | Interrupt | Complete | Custom]:
```

Streaming variant of `resume()`. Same logic — loads checkpoint, sets `_resume_from`, calls `invoke_stream()`.

### In-node retry behavior

When a node retries (LLMRetryableError), the stream yields:
1. `NodeStart` (first attempt)
2. (retry happens internally, no event)
3. `NodeEnd` (on success) or `NodeError` (on final failure)

Failed attempts are logged but not streamed — keeps the event stream clean. If the caller needs attempt-level visibility, they can use `logger` at DEBUG level.

### Interaction with `interrupt()`

`interrupt()` and `stream_event()` are independent:
- `interrupt()` pauses the graph (yields `Interrupt` event, returns state)
- `stream_event()` emits data without pausing (yields `Custom` event, graph continues)
- A node can use both — stream progress while also potentially pausing for human input

## Files changed

| File | Changes |
|------|---------|
| `src/kitegen/llm.py` | Add `chat_stream()` (~25 lines) |
| `src/kitegen/graph.py` | Add 7 event dataclasses, `stream_event()`, `invoke_stream()`, `resume_stream()` (~80 lines) |
| `src/kitegen/__init__.py` | Export new symbols: `chat_stream`, `stream_event`, event types |
| `tests/test_graph.py` | Add streaming tests: basic stream, stream with interrupt+resume, stream with custom events |

## Non-goals

- `chat_structured()` streaming — structured output doesn't stream meaningfully
- Streaming in `invoke()` — use `invoke_stream()` instead; no hybrid mode
- WebSocket / SSE transport — this is an in-process Python API; transports are the caller's concern

## Example: end-to-end

```python
import kitegen as kg
from kitegen.llm import chat_stream

async def generate_title(state):
    prompt = f"Write a title for: {state['product']}"
    chunks = []
    async for chunk in chat_stream("You are a copywriter", prompt):
        await kg.stream_event({"type": "token", "content": chunk})
        chunks.append(chunk)
    state["title"] = "".join(chunks)
    return state

async def review(state):
    await kg.interrupt({"action": "approve", "title": state["title"]})

g = kg.Graph()
g.add_node("generate", generate_title)
g.add_node("review", review)
g.add_edge("generate", "review")
g.set_entry_point("generate")

agent = g.compile(checkpointer=kg.MemorySaver())

# Stream the run
async for event in agent.invoke_stream({"product": "Ceramic Mug"}, thread_id="t1"):
    match event:
        case kg.NodeStart(node=n):
            print(f"\n[{n}] ", end="")
        case kg.Custom(data=d):
            if d.get("type") == "token":
                print(d["content"], end="", flush=True)
        case kg.Interrupt(payload=p):
            print(f"\n⏸ Awaiting approval: {p['title']}")
```

## Migration / backward compatibility

- Existing `chat()`, `invoke()`, `resume()` are unchanged
- New functions are purely additive — no breaking changes
- `invoke_stream()` duplicates `invoke()`'s execution logic (~40 lines); this is acceptable given kitegen's small size. If maintenance burden grows, the shared core can be extracted later.

# kitegen

> **Composable AI workflows in Python. Write once — run as script, API, or worker.**

kitegen is a lightweight agent framework. Everything — Agent, Task, Graph, plain function — is an `Executable` that transforms a plain dict. Compose them freely, stream every step, pause for humans, checkpoint for resilience.

> **Status: Alpha.** Actively developed and dogfooded through the [stock analyst demo](demo/README.md). Core API is stable; expect additions, not breaking changes.

## Why kitegen

Other frameworks conflate tool calling, prompt management, and orchestration — and stop at being a library. kitegen only does orchestration, and deploys anywhere:

- **Everything is Executable** — an Agent is a Graph node; a Graph is a Task; a function is both
- **Plain dicts** — no TypedDict magic, no reducer annotations
- **Human-in-the-loop as a primitive** — `await interrupt(payload)` is a function call
- **Streaming is first-class** — typed events for every node, tool call, and token
- **One-liner deployment** — `to_worker()` today, `to_fastapi()` coming

## Install

```bash
# Not on PyPI yet — install from source
pip install -e .
# Optional extras
pip install -e ".[pydantic]"   # structured output via Pydantic
pip install -e ".[psycopg]"    # Postgres checkpointing
```

## Quick Start — an Agent

```python
import kitegen as kg

@kg.tool
def search(query: str) -> str:
    """Search the web. Schema is inferred from type hints."""
    return f"results for {query}"

agent = kg.Agent(
    role="researcher",
    goal="Find accurate, well-sourced information",
    personality="Meticulous. Verify twice. Cite numbers.",
    tools=[search],
    llm=kg.OpenAIAdapter(model="gpt-4o"),
)

result = await agent.run({"input": "quantum computing in 2026"})
print(result["output"])
```

The agent runs a tool-calling loop: LLM decides → tools execute → results feed back → final answer. `max_iterations` caps the loop; tool errors are fed back so the agent can self-correct.

## Compose — a Graph of Agents

```python
researcher = kg.Agent(role="researcher", goal="...", tools=[...], llm=...)
writer = kg.Agent(role="writer", goal="...", llm=...)

g = kg.Graph()
g.add_node("research", researcher.execute)     # Agent as node
g.add_node("review", human_review)             # plain async fn with interrupt()
g.add_edge("research", "review")
g.set_entry_point("research")

workflow = g.compile(checkpointer=kg.MemorySaver())
async for event in workflow.invoke_stream({"input": "..."}, thread_id="t1"):
    match event:
        case kg.NodeStart(node=n): ...
        case kg.NodeEnd(node=n, trace=t): ...
        case kg.Complete(): ...
```

Checkpoint semantics: reusing a `thread_id` **merges** saved state under new state — new keys win, old keys persist. Interrupted graphs resume via `agent.resume(data, thread_id)`.

## Human-in-the-Loop

```python
async def review_node(state: dict) -> dict:
    if state.get("confidence", 0) < 0.9:
        decision = await kg.interrupt({"action": "review", "item": state["draft"]})
        state["approved"] = decision["approved"]
    return state

# Later, from anywhere:
await workflow.resume({"approved": True}, thread_id="session-1")
```

## Streaming Events

| Event | When |
|-------|------|
| `NodeStart` / `NodeEnd` / `NodeError` | Node lifecycle |
| `ToolCallEvent` / `ToolResultEvent` | Tool execution inside agents |
| `TokenEvent` | LLM token streaming |
| `Interrupt` | Paused for human input |
| `Custom` | Arbitrary data via `await stream_event(...)` |
| `Complete` | Graph finished |

Closing the stream early (client stopped) cancels the in-flight node task — no orphan LLM calls.

## Resilience

```python
tracker = kg.TokenTracker()          # usage + cost per model
cb = kg.CircuitBreaker("llm", failure_threshold=3, recovery_timeout=60)
await cb.call(chat, ...)
raise kg.LLMRetryableError("...")    # retry with exponential backoff
```

## Deployment

```python
# Run any async task on a schedule — exceptions logged, loop survives
await kg.to_worker(my_task, interval=300)      # every 5 minutes
```

## Concepts

| kitegen | LangGraph | Why simpler |
|---------|-----------|-------------|
| `state["key"] = value` | `TypedDict` + `Annotated[list, add_messages]` | Plain dict. No magic reducers |
| `Agent(...)` + `@tool` | 8+ concepts before your first agent | Schema from type hints |
| `await interrupt(payload)` | `Command` class | Function call, not a class |
| `MemorySaver` / `PostgresSaver` (~40 lines) | `PostgresSaver` (1000+ lines) | One row per thread |
| 1 dep (core) — `openai`, optional | 50+ transitive | Bring your own LLM |
| `to_worker(fn, interval)` | DIY asyncio | Deploy as a worker in one line |

## The Demo

[`demo/`](demo/README.md) is a personal stock analyst: a 3-agent pipeline (research → strategy → personalized advice) with portfolio management, market monitoring (stop-loss/take-profit/move alerts + daily briefing), and a streaming React UI. It is the primary test vehicle for the framework.

## Layout

```
src/kitegen/
  core.py       Executable protocol, Context, events, retry
  agent.py      Agent class — ReAct tool-calling loop
  graph.py      Graph state machine — stream, interrupt, checkpoint
  tool.py       @tool decorator with schema inference
  llm.py        LLM adapters — OpenAI, Anthropic, LiteLLM
  deploy.py     to_worker scheduling
  resilience.py Circuit breaker, token/cost tracking
  checkpoint.py Memory / Postgres savers
demo/           Stock analyst (flagship example)
tests/          38 tests
docs/           strategy, design plans, specs
```

## License

MIT

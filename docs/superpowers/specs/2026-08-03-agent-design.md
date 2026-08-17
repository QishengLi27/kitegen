# Agent Design Spec

**Date:** 2026-08-03  
**Status:** Draft  

## Overview

`Agent` is the primary user-facing class. It wraps an LLM, a persona (role/goal/personality), and tools into a single `Executable` that runs a tool-calling loop. Simple case is one line. Complex case composes into a Graph, Task, or Crew — same shape, no new API.

## API

```python
import kitegen as kg

agent = kg.Agent(
    role="researcher",
    goal="Find accurate, well-sourced information about a topic",
    personality="Meticulous and thorough. You verify everything twice. You love bullet points.",
    tools=[search, fetch_url],
    llm=kg.OpenAIAdapter(model="gpt-4o"),
)

# Single call
result = await agent.run({"input": "quantum computing in 2026"})
print(result["output"])

# Streaming
async for event in agent.stream({"input": "quantum computing"}):
    match event:
        case kg.ToolCallEvent(tool=name, arguments=args): ...
        case kg.ToolResultEvent(tool=name, result=r): ...
        case kg.Complete(): ...
```

```python
# Compose into a Graph
g = kg.Graph()
g.add_node("research", agent)                # Agent as node
g.add_node("review", human_review)
g.add_edge("research", "review")
g.set_entry_point("research")
workflow = g.compile()

async for event in workflow.invoke_stream(...):
    ...
```

## Constructor

| Parameter | Type | Default | |
|-----------|------|---------|---|
| `role` | `str` | *required* | Agent's role (e.g. "researcher", "coder") |
| `goal` | `str` | *required* | What the agent should accomplish |
| `personality` | `str \| None` | `None` | Optional personality/tone instructions |
| `tools` | `list[Tool] \| None` | `[]` | Tools available to the agent |
| `llm` | `LLM \| None` | `OpenAIAdapter()` | LLM adapter. Defaults to OpenAI with env vars |
| `max_iterations` | `int` | `10` | Max tool-calling loop iterations |
| `system_prompt` | `str \| None` | `None` | Override the auto-rendered system prompt |
| `output_key` | `str` | `"output"` | Key in state where the final result is stored |
| `input_key` | `str \| None` | `"input"` | Key in state to read user input from. If None, uses the full state |

## System Prompt Rendering

When `system_prompt` is not provided, the agent renders:

```
You are a {role}.
Your goal: {goal}
Your personality: {personality}          # omitted if None

Use the tools available to you to accomplish your goal.
When you have enough information, provide a clear final answer.
```

Users who need a different prompt structure pass `system_prompt="..."` explicitly.

## Execution Loop

```
1. Build messages: [system prompt, user message from state[input_key]]
2. Call llm.chat(messages, tools)
3. If response has tool_calls:
   a. Emit ToolCallEvent via context.stream()
   b. Execute each tool
   c. Emit ToolResultEvent via context.stream()
   d. Add assistant message (with tool_calls) + tool result messages
   e. Loop to step 2
4. If response has content (no tool calls):
   a. Store in state[output_key]
   b. Return state
5. After max_iterations: store last response, return state
```

Tool results are appended as `Message(role="tool", ...)` messages. If a tool raises, the error message is fed back to the LLM so it can self-correct.

## Streaming

The agent emits events through `context.stream()`:

| Event | When |
|-------|------|
| `ToolCallEvent(node, tool, arguments)` | Before each tool execution |
| `ToolResultEvent(node, tool, result)` | After each tool returns |

Token-level streaming (`TokenEvent`) from the agent's LLM calls requires a streaming `chat()` method on the adapter — deferred to a follow-up.

## File

`src/kitegen/agent.py` — new file, ~80 lines.

## Interaction with Graph Streaming

The agent uses `context.stream()` for events. When run directly via `agent.stream()`, events are yielded by `Runnable.stream()` (in `core.py`). When run as a graph node via `graph.invoke_stream()`, the graph currently uses a separate ContextVar-based queue. This means agent events won't surface through graph streaming until the two streaming paths are unified. This is a known limitation documented in the code — not a blocker for the Agent itself.

## Tests

`tests/test_agent.py`:

1. **`test_agent_basic_run`** — Agent without tools returns output in state
2. **`test_agent_with_tools`** — Agent calls a tool, result appears in state
3. **`test_agent_stream_events`** — `agent.stream()` yields ToolCallEvent and ToolResultEvent
4. **`test_agent_max_iterations`** — Agent that always calls tools stops at max_iterations
5. **`test_agent_is_executable`** — Agent implements `Executable` protocol
6. **`test_agent_as_graph_node`** — Agent can be added to a Graph and invoked

## Non-goals

- Memory/chat history management (state + graph nodes handle this)
- Planning/decomposition (Crew handles this)
- Token-level streaming from agent loop (follow-up)
- Streaming integration with graph (`invoke_stream` path — follow-up)

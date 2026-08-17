# kitegen Design & Implementation Plan

> ⚠️ **历史文档（2026-07-24）** — 方向已合并进 [`strategy.md`](strategy.md)，本文保留作为六阶段初稿参考。
> Lightweight agent framework — composable agents, tasks, crews, and graphs.
> Status: superseded by strategy.md

---

## 1. Vision

**kitegen** is a lightweight agent framework for developers who want to spin up LLM agents and workflows quickly, without the boilerplate and lock-in of CrewAI, LangChain, or LangGraph.

It combines the mental models people already know:

- **CrewAI**: agents, tasks, crews, role-based collaboration
- **LangGraph**: explicit state-machine graphs with checkpoints, retries, and human-in-the-loop

But it is significantly smaller and simpler:

- Pure Python. No YAML, no DSL, no visual editor.
- Async-first. Sync functions supported where it makes sense.
- Bring your own LLM, tools, and prompts.
- Every major component is composable: an agent can be a task, a task can be a graph node, a crew can be a graph node, a graph can be a task.

---

## 2. Core Principles

1. **Everything is Executable**  
   `Agent`, `Task`, `Crew`, `Graph`, and plain functions all share a single `Executable` protocol. They can be used interchangeably in crews, graph nodes, and tasks.

2. **Framework handles orchestration; user brings intelligence**  
   kitegen does not ship prompts, model-specific tricks, or tool implementations. It runs your LLM calls, tools, and workflow logic reliably.

3. **Human-in-the-loop is a primitive, not a plugin**  
   Pausing, resuming, and overriding are first-class concepts across agents, crews, and graphs.

4. **Minimal but production-ready**  
   Retry, checkpointing, streaming, tracing, and circuit-breaking are built in. Vector stores, RAG, training, and visual editors are not.

5. **State is just a dict by default**  
   Optional Pydantic/TypedDict schemas can be layered on top when needed.

---

## 3. Target Audience

- Solo developers and small teams building LLM features.
- People who found CrewAI too rigid or LangGraph too verbose.
- Teams that need to ship agents as scripts, APIs, workers, or streaming services without rewriting the workflow.

---

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        kitegen                              │
├─────────────────────────────────────────────────────────────┤
│  API Layer: Agent | Task | Crew | Graph | Tool | LLM       │
├─────────────────────────────────────────────────────────────┤
│  Runtime:  Executable Protocol | Context | Scheduler        │
│            Checkpointing | Streaming | Interrupts | Retries │
└─────────────────────────────────────────────────────────────┘
```

### 4.1 Executable Protocol

All composable components implement the same interface:

```python
class Executable(Protocol):
    async def execute(self, state: dict, context: Context) -> dict:
        ...
```

Implementors:

- `Agent` — runs one LLM turn with tools, returns updated state
- `Task` — renders its template, runs its assigned agent, merges result
- `Crew` — runs a sequence/parallel set of tasks, returns merged state
- `Graph` — runs a compiled graph, returns final state
- `Callable[[dict], Awaitable[dict]]` — wrapped automatically

### 4.2 Context

A `Context` object carries shared execution state and framework services:

```python
@dataclass
class Context:
    thread_id: str
    checkpointer: Checkpointer | None
    stream_queue: asyncio.Queue | None
    retry_policy: RetryPolicy
    interrupt_payload: Any | None  # set when resuming from human input
    node_name: str | None
    agent: Any | None  # back-reference to the executor
```

The `Context` is how executables access checkpointing, emit stream events, and handle interrupts consistently.

---

## 5. Component Specifications

### 5.1 Tool

A plain Python function decorated with `@tool`. The framework infers the JSON schema from the signature.

```python
@kg.tool
def search(query: str, top_k: int = 5) -> str:
    """Search the web for a query and return the top results."""
    return "..."
```

Requirements:

- Async and sync functions supported.
- Schema generated via `inspect.signature` + type hints + docstrings.
- Return type can be a simple value or `ToolResult` for richer metadata.
- Tools are passed to `Agent` instances.

### 5.2 LLM

A thin adapter interface. kitegen provides adapters for common providers; users can implement their own.

```python
class LLM(Protocol):
    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        ...

@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    usage: Usage
```

Built-in adapters:

- `OpenAIAdapter`
- `AnthropicAdapter`
- `LiteLLMAdapter` (covers many providers)

The LLM abstraction is intentionally thin. kitegen is not a universal LLM client; it is an orchestration layer.

### 5.3 Agent

A role-based LLM worker with tools.

```python
researcher = kg.Agent(
    role="researcher",
    goal="Find accurate information about a topic",
    backstory="You are a careful research assistant.",
    tools=[search, calculator],
    llm=kg.OpenAI(model="gpt-4o"),
    max_retries=2,
)

result = await researcher.execute({"topic": "AI frameworks"}, context)
```

Responsibilities:

- Renders system prompt from role/goal/backstory.
- Runs a loop: call LLM → execute tool calls → repeat until done or max iterations.
- Returns updated state.
- Can emit token/tool/stream events via context.

### 5.4 Task

A unit of work assigned to an executable (usually an agent, but could be a crew or graph).

```python
task = kg.Task(
    description="Research {topic} and write a short summary.",
    expected_output="A 2-paragraph summary.",
    agent=researcher,
    output_key="summary",
)

result = await task.execute({"topic": "AI"}, context)
# result["summary"] now contains the output
```

Responsibilities:

- Renders the description template with current state.
- Calls its assigned executable with the rendered prompt/input.
- Extracts output and writes it to `state[output_key]`.
- Supports `context` as input so tasks can reference prior state.

### 5.5 Crew

A team of agents executing tasks.

```python
crew = kg.Crew(
    agents=[researcher, writer],
    tasks=[
        kg.Task("Research {topic}", agent=researcher, output_key="research"),
        kg.Task("Write a blog post based on {research}", agent=writer, output_key="post"),
    ],
    process="sequential",  # sequential | parallel | hierarchical
)

result = await crew.execute({"topic": "AI"}, context)
```

Process types:

- **sequential**: tasks run in order, state passed along.
- **parallel**: tasks run concurrently with the same input state, results merged.
- **hierarchical**: a manager agent delegates tasks to other agents and reviews output.

Responsibilities:

- Schedules task execution.
- Merges task outputs into shared state.
- Handles interrupts and checkpoints across the crew.

### 5.6 Graph

An explicit state-machine execution engine for complex control flow.

```python
graph = kg.Graph()
graph.add_node("research", researcher)          # Agent as node
graph.add_node("write", task)                   # Task as node
graph.add_node("full_crew", crew)               # Crew as node
graph.add_node("subgraph", quality_graph)       # Graph as node
graph.add_node("custom", custom_function)        # Plain function as node

graph.add_edge("research", "write")
graph.add_conditional_edges("write", router, {"ok": "full_crew", "retry": "research"})
graph.set_entry_point("research")

agent = graph.compile()
result = await agent.run({"topic": "AI"})
```

Responsibilities:

- Compiles nodes and edges into a runnable agent.
- Manages execution loop, state, and history.
- Supports interrupts, retries, checkpoints, and streaming.

---

## 6. Execution Model

### 6.1 Entry Points

Every executable exposes a high-level run method:

```python
# Direct execution
result = await researcher.run({"topic": "AI"})
result = await crew.run({"topic": "AI"})
result = await agent.run({"topic": "AI"})

# With explicit context
result = await crew.execute(state, context)
```

### 6.2 Streaming

Streaming is first-class. All executables can produce events:

```python
async for event in crew.stream({"topic": "AI"}):
    match event:
        case kg.NodeStart(node=name): ...
        case kg.Token(content=c): ...
        case kg.ToolCall(name=n, args=a): ...
        case kg.Interrupt(payload=p): ...
        case kg.Complete(): ...
```

### 6.3 Interrupts and Resume

Executables can pause execution and wait for human input:

```python
async def review(state, context):
    if context.should_interrupt("review", state):
        decision = await kg.interrupt({"action": "review", "payload": state})
        state["approved"] = decision["approved"]
    return state
```

Resume:

```python
result = await agent.resume({"approved": True}, thread_id="session-1")
```

### 6.4 Checkpoints

Checkpoints are persisted through the `Checkpointer` interface:

```python
class Checkpointer(Protocol):
    async def save(self, state: dict, thread_id: str, step: int | None = None) -> None: ...
    async def load(self, thread_id: str, step: int | None = None) -> dict | None: ...
```

Built-in implementations:

- `MemorySaver` — in-memory, process-local.
- `PostgresSaver` — PostgreSQL with JSONB.

Checkpointing is optional but recommended for production.

### 6.5 Retries and Resilience

- Nodes/tasks that raise `LLMRetryableError` are retried with exponential backoff.
- `CircuitBreaker` can wrap external calls (LLMs, APIs).
- Per-node timeouts will be supported.

---

## 7. Design Decisions

### 7.1 State as plain dict

Default state is a plain Python dict. This keeps the framework simple and unopinionated. Users can add Pydantic models or TypedDict schemas on top if they want validation.

### 7.2 No YAML / no visual editor

Configuration is code. This is a deliberate choice to keep the framework lightweight and version-control-friendly.

### 7.3 LLM abstraction is thin

kitegen does not try to normalize every LLM feature. It provides a small adapter interface and a few common implementations. Users bring their own clients for advanced features.

### 7.4 Tool schema inference

Tool schemas are inferred from Python type hints and docstrings. No manual JSON schema required for simple cases. Users can override schemas when needed.

### 7.5 Composability over specialization

Instead of separate frameworks for agents, crews, and graphs, kitegen treats them as the same abstraction: executables that transform state. This is the main architectural differentiator.

---

## 8. Out of Scope (at least for now)

| Feature | Reason |
|---|---|
| Built-in vector store / RAG | Too opinionated; users bring their own |
| Prompt template library | Users bring their own prompts |
| Training / fine-tuning | Out of scope for an orchestration framework |
| Visual workflow editor | Large maintenance burden |
| Built-in agent marketplace | Application layer, not framework |
| Universal LLM feature parity | Thin adapters, not full client SDK |

---

## 9. Roadmap

### Phase 1: Foundation

| # | Task | Deliverable | Status |
|---|---|---|---|
| 1.1 | Define `Executable` protocol and `Context` API | `src/kitegen/core.py` | ✅ done |
| 1.2 | Implement `@tool` decorator with schema inference | `src/kitegen/tool.py` + tests | ✅ done |
| 1.3 | Build minimal LLM adapter interface + OpenAI adapter | `src/kitegen/llm.py` + tests | ✅ done |
| 1.4 | Add Anthropic and LiteLLM adapters | `src/kitegen/llm.py` | ✅ done |

### Phase 2: Core Agents

| # | Task | Deliverable | Status |
|---|---|---|---|
| 2.1 | Build `Agent` class with role/goal/tools/LLM loop | `src/kitegen/agent.py` + tests | ✅ done |
| 2.2 | Add tool-calling loop inside agent | tests + example | ✅ done |
| 2.3 | Build `Task` class (template + assigned executable) | `src/kitegen/task.py` + tests | pending |
| 2.4 | Build `Crew` class with sequential execution | `src/kitegen/crew.py` + tests | pending |
| 2.5 | Add parallel crew execution | `src/kitegen/crew.py` | pending |
| 2.6 | Add hierarchical crew process | `src/kitegen/crew.py` | pending |

### Phase 3: Composition

| # | Task | Deliverable | Status |
|---|---|---|---|
| 3.1 | Refactor `Graph.add_node` to accept any `Executable` | `src/kitegen/graph.py` + tests | ◐ partial（`agent.execute` 可作 node；`as_executable` 包装未接入） |
| 3.2 | Ensure `Agent`, `Task`, `Crew`, `Graph` all implement `Executable` | tests | ◐ partial（Agent/Graph ✅，Task/Crew 未建） |
| 3.3 | Add subgraph support | `src/kitegen/graph.py` | pending |
| 3.4 | Add fan-out / fan-in execution pattern | `src/kitegen/graph.py` | pending |

### Phase 4: Production Features

| # | Task | Deliverable | Status |
|---|---|---|---|
| 4.1 | Add human-in-the-loop primitive across all executables | `src/kitegen/interrupt.py` | ◐ partial（graph 中断/恢复 ✅；审批/超时/审计升级 pending → strategy F5） |
| 4.2 | Add per-node timeouts | `src/kitegen/graph.py` | pending |
| 4.3 | Harden streaming event handling | `src/kitegen/graph.py` | ◐ partial（流取消 ✅、checkpoint merge ✅；双流式统一 pending → strategy F1） |
| 4.4 | Add checkpoint versioning | `src/kitegen/checkpoint.py` | pending |
| 4.5 | Add observability hooks (on_node_start, on_node_end, etc.) | `src/kitegen/observability.py` | pending → strategy F8（最小 trace，不接 OTel） |

### Phase 5: Developer Experience

| # | Task | Deliverable | Status |
|---|---|---|---|
| 5.1 | Add graph visualization export (Mermaid) | `src/kitegen/viz.py` | pending |
| 5.2 | Create example gallery | `examples/` | ✅ done（`demo/` 股票助手为 flagship example） |
| 5.3 | Write migration guide from LangGraph / CrewAI | `docs/` | pending |
| 5.4 | Add FastAPI / worker runner examples | `examples/` | ✅ done（demo 手写 FastAPI+SSE；框架化 → strategy F4） |
| 5.5 | Build MkDocs site | `docs/` | pending → strategy F3 |

### Phase 6: Community & Release

| # | Task | Deliverable | Status |
|---|---|---|---|
| 6.1 | Set up GitHub Actions CI/CD | `.github/workflows/` | pending |
| 6.2 | Add CONTRIBUTING.md and issue templates | repo root | pending |
| 6.3 | Automate PyPI releases on GitHub tags | `.github/workflows/` | pending → strategy F3 |
| 6.4 | Launch blog post / Show HN | external | pending |
| 6.5 | Create plugin interface for community adapters | `src/kitegen/plugins.py` | pending |

---

## 10. Open Questions

1. Should `Agent` run a multi-turn tool loop internally, or should each tool call be a graph node?  
   *Recommendation: internal loop for simplicity, but expose tool-call events for streaming.*

2. Should the default state be a plain dict or a `kitegen.State` wrapper with helper methods?  
   *Recommendation: plain dict by default, optional typed wrappers.*

3. How should tool errors be handled? Retry at tool level, agent level, or graph level?  
   *Recommendation: tool-level retry for transient errors, graph-level for node failures.*

4. Should crews support dynamic task generation (manager creates tasks at runtime)?  
   *Recommendation: yes, but only in hierarchical process.*

5. Should streaming be push- or pull-based for all executables?  
   *Recommendation: pull-based (`async for event`) with a shared event protocol.*

---

## 11. Next Immediate Steps

1. Approve this design document or mark revisions.
2. Implement Phase 1.1: `Executable` protocol and `Context`.
3. Implement Phase 1.2: `@tool` decorator with schema inference.
4. Implement Phase 1.3: minimal LLM adapter interface.

---

## 12. Glossary

- **Executable**: any component that can transform state via `execute(state, context)`.
- **Context**: runtime services available to executables (checkpointing, streaming, interrupts).
- **Agent**: role-based LLM worker with tools.
- **Task**: a templated unit of work assigned to an executable.
- **Crew**: a team of agents executing tasks.
- **Graph**: explicit state-machine workflow engine.
- **Checkpointer**: persistence interface for state.
- **Interrupt**: a pause in execution waiting for human input.

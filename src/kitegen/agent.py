"""kitegen.agent — Role-based LLM agent with tools.

Usage:
    agent = Agent(
        role="researcher",
        goal="Find accurate information about a topic",
        tools=[search, calculator],
        llm=OpenAIAdapter(model="gpt-4o"),
    )
    result = await agent.run({"input": "quantum computing"})

Agents are Executables — they can be graph nodes, task runners, or standalone.
"""

from __future__ import annotations

from typing import Any

from kitegen.core import (
    Context,
    Runnable,
    ToolCallEvent,
    ToolResultEvent,
)
from kitegen.llm import LLMAdapter, Message, OpenAIAdapter
from kitegen.tool import Tool


class Agent(Runnable):
    """A role-based LLM agent with tools.

    Runs a ReAct-style loop: call LLM → execute tools → feed results → repeat.
    Implements ``Runnable``, so it works standalone or as a graph node.
    """

    def __init__(
        self,
        role: str,
        goal: str,
        *,
        personality: str | None = None,
        tools: list[Tool] | None = None,
        llm: LLMAdapter | None = None,
        max_iterations: int = 5,
        system_prompt: str | None = None,
        input_key: str = "input",
        output_key: str = "output",
        output_schema: type | None = None,
        memory: Any | None = None,
    ):
        self.role = role
        self.goal = goal
        self.personality = personality
        self.tools = tools or []
        self.llm = llm or OpenAIAdapter()
        self.max_iterations = max_iterations
        self._system_prompt = system_prompt
        self.input_key = input_key
        self.output_key = output_key
        # Structured output: the final answer is parsed into an instance of
        # this type (Pydantic BaseModel via model_validate_json, or any class
        # constructed from the parsed dict). Parse failures raise ValueError.
        self.output_schema = output_schema
        # Pluggable memory (see kitegen.memory): previous exchanges are
        # injected between the system prompt and the current user message.
        self.memory = memory

    # ── System prompt ──────────────────────────────────────────────────

    def _render_system_prompt(self) -> str:
        if self._system_prompt:
            return self._system_prompt

        parts = [
            f"You are a {self.role}.",
            f"Your goal: {self.goal}",
        ]
        if self.personality:
            parts.append(f"Your personality: {self.personality}")

        if self.tools:
            parts.append(
                "TOOL USAGE RULES (critical):\n"
                "- Call a tool ONLY when you need information you don't have.\n"
                "- After receiving tool results, DO NOT call any more tools.\n"
                "- Analyze the tool output and give your final answer immediately."
            )
        else:
            parts.append("Provide a clear, direct answer.")

        return "\n\n".join(parts)

    # ── Input extraction ───────────────────────────────────────────────

    def _build_user_message(self, state: dict[str, Any]) -> str:
        """Extract the user's input from state."""
        if self.input_key in state:
            return str(state[self.input_key])
        # Fallback: use the whole state as context
        public = {k: v for k, v in state.items() if not k.startswith("_")}
        return str(public) if public else ""

    # ── Execute ────────────────────────────────────────────────────────

    async def execute(
        self, state: dict[str, Any], context: Context | None = None
    ) -> dict[str, Any]:
        """Run the agent's tool-calling loop. Updates and returns state.

        ``context`` is optional — when called as a Graph node, a default
        Context is created automatically.
        """
        if context is None:
            context = Context()
        node_name = context.node_name or self.role

        user_text = self._build_user_message(state)
        messages: list[Message] = [Message(role="system", content=self._render_system_prompt())]
        if self.memory is not None:
            messages.extend(self.memory.get())  # previous exchanges as context
        messages.append(Message(role="user", content=user_text))

        tool_schemas = [t.to_openai_schema() for t in self.tools] if self.tools else None
        tool_map = {t.name: t for t in self.tools}

        import logging
        _log = logging.getLogger("kitegen.agent")

        for iteration in range(self.max_iterations):
            _log.info("[%s] iteration %d/%d, %d messages", self.role, iteration + 1,
                      self.max_iterations, len(messages))

            response = await self.llm.chat(
                messages,
                tools=tool_schemas,
            )

            _log.info("[%s] LLM response: content=%s, tool_calls=%s",
                      self.role,
                      (response.content or "")[:100],
                      [tc.name for tc in (response.tool_calls or [])])

            # Tool calls — execute them and feed results back
            if response.tool_calls:
                # Add assistant message with tool calls
                messages.append(
                    Message(role="assistant", tool_calls=response.tool_calls)
                )

                for tc in response.tool_calls:
                    context.stream(
                        ToolCallEvent(
                            node=node_name,
                            tool=tc.name,
                            arguments=tc.arguments,
                        )
                    )

                    tool = tool_map.get(tc.name)
                    if tool:
                        try:
                            result = await tool.invoke(tc.arguments, context)
                        except Exception as exc:
                            _log.error("[%s] tool '%s' failed: %s", self.role, tc.name, exc)
                            result = f"Error: {exc}"
                    else:
                        result = f"Error: unknown tool '{tc.name}'"

                    context.stream(
                        ToolResultEvent(
                            node=node_name,
                            tool=tc.name,
                            result=result,
                        )
                    )

                    messages.append(
                        Message(
                            role="tool",
                            content=str(result),
                            tool_call_id=tc.id,
                        )
                    )

                # Inject instruction to stop calling tools
                messages.append(
                    Message(
                        role="user",
                        content=(
                            "You now have the information you need. "
                            "Provide your final analysis now. Do NOT call any more tools."
                        ),
                    )
                )

                continue  # Loop: feed results back to LLM

            # Final answer — no tool calls
            _log.info("[%s] final answer (%d chars)", self.role, len(response.content or ""))
            content = response.content or ""
            state[self.output_key] = self._finalize_output(content, user_text)
            return state

        _log.warning("[%s] max iterations (%d) reached — returning last response", self.role, self.max_iterations)

        # Max iterations reached — use the last response as the answer
        content = response.content or "Unable to complete analysis."
        state[self.output_key] = self._finalize_output(content, user_text)
        return state

    def _finalize_output(self, content: str, user_text: str) -> Any:
        """Parse structured output if a schema is set; record the exchange
        in memory only after a successful parse (a failed parse must not
        pollute the agent's memory with garbage)."""
        if self.output_schema is None:
            if self.memory is not None:
                self.memory.add("user", user_text)
                self.memory.add("assistant", content)
            return content

        import json
        import re

        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE
        ).strip()

        # Pydantic BaseModel
        if hasattr(self.output_schema, "model_validate_json"):
            parsed = self.output_schema.model_validate_json(cleaned)
        else:
            # Plain dataclass / constructor — parse dict and splat
            try:
                data = json.loads(cleaned)
                parsed = self.output_schema(**data)
            except Exception as e:
                raise ValueError(
                    f"Failed to parse structured output as "
                    f"{getattr(self.output_schema, '__name__', self.output_schema)}: {e}\n"
                    f"Raw output: {content[:300]}"
                ) from e

        if self.memory is not None:
            self.memory.add("user", user_text)
            self.memory.add("assistant", content)
        return parsed

"""kitegen.tool — Tool decorator and JSON schema inference.

A tool is a plain Python function decorated with `@kg.tool`. The framework infers
the JSON schema from the function signature and docstring, so tools can be passed
to LLMs without manual schema writing.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, get_origin, get_args

from kitegen.core import Context, Executable, Runnable, execute_with_retry, LLMRetryableError


@dataclass
class Tool:
    """Represents a callable tool with a JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    returns_directly: bool = False

    async def invoke(self, arguments: dict[str, Any], context: Context) -> Any:
        """Call the tool, filtering arguments to only what the function accepts."""
        import logging
        _log = logging.getLogger("kitegen.tool")

        sig = inspect.signature(self.fn)
        filtered: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name == "context" and param.annotation is Context:
                filtered[name] = context
            elif name in arguments:
                filtered[name] = arguments[name]

        if set(arguments) - set(filtered):
            _log.info("[%s] Dropped extra args: %s", self.name, set(arguments) - set(filtered))

        _log.info("[%s] invoke args=%s filtered=%s", self.name, arguments, filtered)
        result = self.fn(**filtered)
        if asyncio.iscoroutine(result):
            result = await result

        preview = str(result)[:200]
        _log.info("[%s] result (%d chars): %s", self.name, len(str(result)), preview)
        return result

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the tool in OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Return the tool in Anthropic tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# ── Schema inference ─────────────────────────────────────────────────────────


_SIMPLE_SCHEMAS: dict[type, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    Any: {},
}


def _type_to_schema(tp: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON schema fragment."""
    origin = get_origin(tp)
    args = get_args(tp)

    # Optional[T] -> Union[T, None]
    if origin is type | None or (origin is not None and type(None) in args):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            inner = _type_to_schema(non_none[0])
            return {**inner, "nullable": True}
        return {"anyOf": [_type_to_schema(a) for a in non_none], "nullable": True}

    if origin is list or origin is list:
        items = _type_to_schema(args[0]) if args else {}
        return {"type": "array", "items": items}

    if origin is dict:
        return {"type": "object"}

    if origin is not None:
        # Unsupported generic; fall back to permissive schema
        return {}

    return dict(_SIMPLE_SCHEMAS.get(tp, {}))


def _extract_param_descriptions(docstring: str | None) -> dict[str, str]:
    """Extract minimal Google/NumPy-style Args descriptions from docstring."""
    if not docstring:
        return {}

    descriptions: dict[str, str] = {}
    lines = docstring.strip().splitlines()
    in_args = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "parameters:"):
            in_args = True
            continue
        if in_args:
            if stripped == "" or stripped.endswith(":") and stripped.lower() not in (
                "args:", "arguments:", "parameters:"
            ):
                # Next section
                if any(c.isalpha() for c in stripped):
                    break
                continue
            # Parse "name: description" or "name (type): description"
            if ":" in stripped:
                name_part, desc = stripped.split(":", 1)
                name = name_part.split()[0]
                descriptions[name] = desc.strip()
    return descriptions


def _build_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """Build a JSON schema for a function's parameters."""
    sig = inspect.signature(fn)
    type_hints = getattr(fn, "__annotations__", {})
    docstring = inspect.getdoc(fn)
    param_descriptions = _extract_param_descriptions(docstring)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name == "context" and type_hints.get(name) is Context:
            # Allow tools to receive the execution context explicitly
            continue

        tp = type_hints.get(name, Any)
        schema = _type_to_schema(tp)
        if getattr(param, "description", None) or param_descriptions.get(name):
            schema["description"] = param_descriptions.get(name, "")

        # If there is no default, mark required
        if param.default is inspect.Parameter.empty:
            required.append(name)

        properties[name] = schema

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _build_description(fn: Callable[..., Any]) -> str:
    """Return the first paragraph of the function docstring."""
    doc = inspect.getdoc(fn)
    if not doc:
        return f"Tool {fn.__name__}"
    return doc.strip().split("\n\n")[0].strip()


# ── Decorator ────────────────────────────────────────────────────────────────


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    schema: dict[str, Any] | None = None,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """Decorate a function as a kitegen tool.

    Examples:
        @kg.tool
        def search(query: str) -> str:
            \"\"\"Search the web.\"\"\"
            return ...

        @kg.tool(name="calc", description="Evaluate a math expression.")
        def calculator(expression: str) -> float:
            return eval(expression)
    """

    def _decorator(func: Callable[..., Any]) -> Tool:
        return Tool(
            name=name or func.__name__,
            description=description or _build_description(func),
            parameters=schema or _build_schema(func),
            fn=func,
        )

    if fn is not None:
        return _decorator(fn)
    return _decorator


async def call_tool(
    tool: Tool,
    arguments: dict[str, Any],
    context: Context,
) -> Any:
    """Invoke a tool with retry policy from context."""
    return await execute_with_retry(tool.invoke, arguments, context=context)


__all__ = ["Tool", "tool", "call_tool"]

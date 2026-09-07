from __future__ import annotations

import inspect
import types
from dataclasses import dataclass
from typing import Any, Callable, get_args, get_origin, get_type_hints


def _json_schema(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {types.UnionType} or (origin is not None and type(None) in args):
        non_null = [item for item in args if item is not type(None)]
        return _json_schema(non_null[0]) if len(non_null) == 1 else {}
    if origin is list or annotation is list:
        item_type = args[0] if args else Any
        return {"type": "array", "items": _json_schema(item_type)}
    if origin is dict or annotation is dict:
        return {"type": "object"}
    return {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
    }.get(annotation, {"type": "string"})


@dataclass(slots=True)
class RegisteredTool:
    name: str
    description: str
    function: Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, name: str, description: str, function: Callable[..., Any]) -> None:
        self._tools[name] = RegisteredTool(name, description, function)

    def schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for tool in sorted(self._tools.values(), key=lambda item: item.name):
            signature = inspect.signature(tool.function)
            hints = get_type_hints(tool.function)
            properties = {
                name: _json_schema(hints.get(name, parameter.annotation))
                for name, parameter in signature.parameters.items()
            }
            required = [
                name
                for name, parameter in signature.parameters.items()
                if parameter.default is inspect.Parameter.empty
            ]
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {"type": "object", "properties": properties, "required": required},
                    },
                }
            )
        return schemas

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tools[name]
        result = tool.function(**arguments)
        return await result if inspect.isawaitable(result) else result

from __future__ import annotations
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, Callable, TypeVar
import typing

if typing.TYPE_CHECKING:
    from pydantic import BaseModel

    from mybot.agent.tools.context import ToolContext

_ToolT = TypeVar("_ToolT", bound="Tool")
# Matches :meth:`Tool._cast_value` / :meth:`Schema.validate_json_schema_value` behavior
_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class Schema:

    @staticmethod
    def resolve_json_schema_type(t: Any) -> str | None:
        if isinstance(t, list):
            return next((x for x in t if x != "null"), None)
        return t

    @staticmethod
    def fragment(schema: Any) -> dict[str, Any]:
        """Convert a Schema instance or raw dict into a JSON Schema dict."""
        if isinstance(schema, dict):
            return schema
        if hasattr(schema, "to_json_schema"):
            return schema.to_json_schema()
        raise TypeError(f"Cannot convert {type(schema)} to JSON Schema fragment")


class Tool(ABC):
    _TYPE_MAP = _JSON_TYPE_MAP
    _BOOL_TRUE = frozenset(("true", "1", "yes"))
    _BOOL_FALSE = frozenset(("false", "0", "no"))

    @staticmethod
    def _resolve_type(t: Any) -> str | None:
        return Schema.resolve_json_schema_type(t)

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        ...

    def to_schema(self) -> dict[str, Any]:
        """Return OpenAI function-calling schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            return params
        return self._cast_object(params, schema)

    @classmethod
    def _cast_value(cls, value: Any, schema: dict[str, Any]) -> Any:
        """Coerce a single value to match its JSON Schema type."""
        json_type = schema.get("type")
        resolved = cls._resolve_type(json_type)
        if resolved is None or value is None:
            return value
        expected = cls._TYPE_MAP.get(resolved)
        if expected is None:
            return value
        if isinstance(value, expected):
            return value
        # Boolean special handling
        if expected is bool:
            if isinstance(value, str):
                return value.lower().strip() in cls._BOOL_TRUE
            return bool(value)
        try:
            return expected(value)
        except (ValueError, TypeError):
            return value

    def _cast_object(
        self, params: dict[str, Any], schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Cast each param value according to its property schema."""
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return params
        out: dict[str, Any] = {}
        for key, val in params.items():
            prop_schema = properties.get(key)
            if isinstance(prop_schema, dict):
                out[key] = self._cast_value(val, prop_schema)
            else:
                out[key] = val
        return out

    config_key: str = ""
    _plugin_discoverable: bool = True
    _scopes: set[str] = {"core"}

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return True

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls()


def tool_parameters(schema: dict[str, Any]) -> Callable[[type[_ToolT]], type[_ToolT]]:
    """Class decorator: attach JSON Schema and inject a concrete ``parameters`` property."""

    def decorator(cls: type[_ToolT]) -> type[_ToolT]:
        frozen = deepcopy(schema)

        @property
        def parameters(self: Any) -> dict[str, Any]:
            return deepcopy(frozen)

        cls.parameters = parameters

        abstract = getattr(cls, "__abstractmethods__", None)
        if abstract is not None and "parameters" in abstract:
            cls.__abstractmethods__ = frozenset(abstract - {"parameters"})

        return cls

    return decorator

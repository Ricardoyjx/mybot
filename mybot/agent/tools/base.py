from __future__ import annotations
from abc import ABC, abstractmethod
from copy import deepcopy
from re import A
from tkinter import NO
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

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            return params
        return self._cast_object(params, schema)

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

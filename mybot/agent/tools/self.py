from __future__ import annotations

from typing import Any

from mybot.agent.tools.base import Tool, tool_parameters
from mybot.agent.tools.schema import tool_parameters_schema, StringSchema


@tool_parameters(
    tool_parameters_schema(
        message=StringSchema("A message to echo back."),
        required=["message"],
    )
)
class MyTool(Tool):
    """Placeholder self-reference tool for runtime state."""

    _scopes = {"core"}

    @property
    def name(self) -> str:
        return "my"

    @property
    def description(self) -> str:
        return "Internal placeholder tool. Echoes back the given message."

    async def execute(self, message: str = "", **kwargs: Any) -> str:
        return f"Echo: {message}"

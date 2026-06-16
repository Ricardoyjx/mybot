from agent.tools import mcp as mcp_tools
from agent.tools.registry import ToolRegistry
from typing import Any


async def connect_mcp(state: any, tools: ToolRegistry) -> None:
    return await mcp_tools.connect_missing_servers(state, tools)


class ContextBuilder:

    def build_messages() -> list[dict[str, Any]]:
        return []

    pass

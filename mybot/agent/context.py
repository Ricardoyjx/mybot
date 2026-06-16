from mybot.agent.tools import mcp as mcp_tools
from mybot.agent.tools.registry import ToolRegistry
from typing import Any


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    return await mcp_tools.connect_missing_servers(state, tools)


class ContextBuilder:

    def build_messages(
        self,
        user_message: str,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        return [
            {"role": "user", "content": user_message},
        ]

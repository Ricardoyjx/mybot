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
        history: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if history:
            for item in history:
                role = item.get("role")
                content = item.get("content")
                if role in {"user", "assistant"} and isinstance(content, str):
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        return messages

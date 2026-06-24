from mybot.agent.tools import mcp as mcp_tools
from mybot.agent.tools.registry import ToolRegistry
from typing import Any


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    return await mcp_tools.connect_missing_servers(state, tools)


DEFAULT_SYSTEM_PROMPT = (
    "你是一个有用的 AI 助手。你可以通过调用工具来完成任务，"
    "例如读取文件、搜索信息等。当用户要求你读取或分析文件时，调用工具。"
)


class ContextBuilder:

    def build_messages(
        self,
        user_message: str,
        session_id: str = "",
        history: list[dict[str, Any]] | None = None,
        system_prompt=None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        messages = [
            {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}
        ]

        if history:
            for item in history:
                role = item.get("role")
                content = item.get("content")
                if role in {"user", "assistant"} and isinstance(content, str):
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        return messages

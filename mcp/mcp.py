from tool.tool import Tool
from typing import Any
from collections.abc import Awaitable, Callable


def _normalize_schema_for_openai(schema: any) -> dict[str, Any]:
    """Normalize only nullable JSON Schema patterns for tool definitions."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    normalized = dict(schema)

    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]

    pass


_ReconnectCallback = Callable[[str, str, Tool], Awaitable[Tool | None]]


def _is_session_terminated(exc: BaseException) -> bool:

    messages = [str(exc)]
    error = getattr(exc, "error", None)
    if error is not None:
        messages.append(str(getattr(error, "message", "")))
    return any(
        marker in message.lower()
        for marker in ("session terminated", "connection closed")
        for message in messages
    )


class _MCPWrapperBase(Tool):
    """Common reconnect handling for wrappers bound to one MCP server session."""

    _plugin_discoverable = False

    def _set_mcp_connection(self, session: Any, server_name: str) -> None:
        self._session = session
        self._server_name = server_name
        self._reconnect: _ReconnectCallback | None = None

    def set_reconnect_handler(self, reconnect: _ReconnectCallback) -> None:
        self._reconnect = reconnect

    async def _refresh_session_after_termination(
        self,
        exc: BaseException,
        already_refreshed: bool,
        capability_kind: str,
    ) -> bool:
        if (
            already_refreshed
            or not _is_session_terminated(exc)
            or self._reconnect is None
        ):
            return False
        refreshed_tool = await self._reconnect(self._server_name, self._name, self)
        refreshed_session = getattr(refreshed_tool, "_session", None)
        refreshed_session = refreshed_tool._session


class MCPToolWrapper(_MCPWrapperBase):
    """将远程mcp调用包装为本地工具接口"""

    def __init__(self, server_name, tool_spec):
        self.name = f"mcp_{server_name}_{tool_spec.name}"
        self.description = tool_spec.description
        self.schema = self._normalize_schema(tool_spec.input_schema)

    async def run(self, args):
        # 将本地调用转换为 MCP 协议的远程调用
        result = await self.mcp_client.call_tool(self.original_name, args)
        return result

    def _normalize_schema(self, schema):
        """将 MCP schema 转换为 OpenAI 兼容格式"""
        return self._normalize_schema

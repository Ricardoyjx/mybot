from contextlib import AsyncExitStack, suppress
from mybot.agent.tools.base import Tool
from mybot.agent.tools import ToolRegistry
from typing import Any
from collections.abc import Awaitable, Callable
from loguru import logger
import asyncio
import os
import shutil
import sys


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


def _normalize_windows_stdio_command(
    command: str,
    args: list[str] | None,
    env: dict[str, str] | None,
) -> tuple[str, list[str], dict[str, str] | None]:
    """Wrap .cmd/.bat launchers with ``cmd /c`` on Windows.

    ``asyncio.create_subprocess_exec`` (used by the MCP stdio transport)
    cannot invoke ``.cmd`` / ``.bat`` files directly.  When the resolved
    command is one of these shell scripts, we prepend ``cmd /c`` so the
    Windows command processor handles the invocation.

    Non-Windows platforms are returned unchanged.
    """
    normalize_arg = list(args or [])
    if os.name != "nt":
        return command, normalize_arg, env

    if sys.platform == "win32":
        resolved = shutil.which(command)
        if resolved:
            ext = os.path.splitext(resolved)[1].lower()
            if ext in (".cmd", ".bat"):
                return "cmd", ["/c", command] + (args or []), env
        # Fallback: bare command with .cmd/.bat extension in the name itself
        ext = os.path.splitext(command)[1].lower()
        if ext in (".cmd", ".bat"):
            return "cmd", ["/c", command] + (args or []), env
    return command, args or [], env


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


class MCPResourceWrapper(_MCPWrapperBase):
    """Wraps an MCP resource URI as a read-only nanobot Tool."""

    __plugin_discoverable = False

    def __init__(
        self, session, server_name: str, resource_def, resource_timeout: int = 30
    ):
        self._set_mcp_connection(session, server_name)
        self._uri = resource_def.uri
        desc = resource_def.description or resource_def.name
        self._description = f"[MCP Resource] {desc}\nURI: {self._uri}"
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        self._resource_timeout = resource_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.read_resource(self._uri),
                    timeout=self._resource_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP resource '{}' timed out after {}s",
                    self._name,
                    self._resource_timeout,
                )
                return f"(MCP resource read timed out after {self._resource_timeout}s)"
            else:
                parts: list[str] = []
                for block in result.contents:
                    if isinstance(block, types.TextResourceContents):
                        parts.append(block.text)
                    elif isinstance(block, types.BlobResourceContents):
                        parts.append(f"[Binary resource: {len(block.blob)} bytes]")
                    else:
                        parts.append(str(block))
                return "\n".join(parts) or "(no output)"


async def connect_missing_servers(state: Any, registry: ToolRegistry) -> None:
    """Connect configured MCP servers that are not currently live."""
    missing_servers = {
        name: cfg
        for name, cfg in state._mcp_servers.items()
        if name not in state._mcp_stacks
    }
    if state._mcp_connecting or not missing_servers:
        return
    state._mcp_connecting = True
    try:
        connected = await connect_mcp_servers(missing_servers, registry)
        state._mcp_stacks.update(connected)

        if connected:
            logger.info("MCP connected servers: {}", sorted(connected))
        else:
            logger.warning(
                "No MCP servers connected successfully (will retry next message)"
            )
    except asyncio.CancelledError:
        logger.warning("MCP connection cancelled (will retry next message)")
        state._mcp_connected = bool(state._mcp_stacks)
    except BaseException as e:
        logger.warning("Failed to connect MCP servers (will retry next message): {}", e)
        state._mcp_connected = bool(state._mcp_stacks)
    finally:
        state._mcp_connecting = False


async def connect_mcp_servers(
    mcp_server: dict, registry: ToolRegistry
) -> dict[str, AsyncExitStack]:
    """连接到已配置的 MCP 服务器，并注册它们的工具、资源和提示词（prompts）。
    返回一个字典，将服务器名称映射到其专属的 AsyncExitStack（异步退出栈）。
    每个服务器都会分配一个独立的栈，以防止在配置了多个 MCP 服务器时发生取消作用域（cancel scope）冲突"""

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def connect_single_server(
        name: str, cfg
    ) -> tuple[str, AsyncExitStack | None]:
        server_stack = AsyncExitStack()
        await server_stack.__aenter__()

        # todo 判断transport_type值: url,see,streamableHttp
        try:
            transport_type = cfg.type
            if not transport_type:
                if cfg.command:
                    transport_type = "stdio"

            if transport_type == "stdio":
                command, args, env = _normalize_windows_stdio_command(
                    cfg.command,
                    cfg.args,
                    cfg.env or None,
                )
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env,
                    cwd=cfg.cwd or None,
                )
                read, write = await server_stack.enter_async_context(
                    stdio_client(params)
                )
            else:
                logger.warning(
                    "MCP server '{}': Unknown transport type '{}',",
                    name,
                    transport_type,
                )
                await server_stack.aclose()
                return name, None

            session = await server_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            enabled_tools = set(cfg.enable_tools)

            # get resources
            try:
                resources_result = await session.list_resources()
                for resource in resources_result.resources:
                    wrapper = MCPResourceWrapper(  # todo impl
                        session, name, resource, resource_timeout=cfg.tool_timeout
                    )
                    registry.register(wrapper)
                    registered_count += 1
                    logger.debug(
                        "MCP: registered resource '{}' from server '{}'",
                        wrapper.name,
                        name,
                    )
            except Exception as e:
                logger.debug(
                    "MCP server '{}': resources not supported or failed: {}", name, e
                )

            # get prompt
            try:
                prompts_result = await session.list_prompts()
                for prompt in prompts_result.prompts:
                    wrapper = MCPPromptWrapper(  # todo impl
                        session, name, prompt, prompt_timeout=cfg.tool_timeout
                    )
                    registry.register(rwapper)
                    registered_count += 1
                    logger.debug(
                        "MCP: registered prompt '{}' from server '{}'",
                        wrapper.name,
                        name,
                    )
            except Exception as e:
                logger.debug(
                    "MCP server '{}': prompts not supported or failed: {}", name, e
                )

            logger.info(
                "MCP server '{}': connected, {} capabilities registered",
                name,
                registered_count,
            )

            return name, server_stack
        # exception handler
        except Exception as e:
            hint = ""
            text = str(e).lower()
            if any(
                marker in text
                for marker in (
                    "parse error",
                    "invalid json",
                    "unexpected token",
                    "jsonrpc",
                    "content-length",
                )
            ):
                hint = (
                    " Hint: this looks like stdio protocol pollution. Make sure the MCP server writes "
                    "only JSON-RPC to stdout and sends logs/debug output to stderr instead."
                )
            logger.exception("MCP server '{}': failed to connect: {}", name, hint)
            with suppress(Exception):
                await server_stack.aclose()
            return name, None

from contextlib import AsyncExitStack, suppress
from http import server
from tkinter import E
from mybot.agent.tools.base import Tool
from mybot.agent.tools import ToolRegistry
from typing import Any
from collections.abc import Awaitable, Callable
from loguru import logger
import asyncio
import os
import shutil
import sys
import re

_SANITIZE_RE = re.compile(r"_+")

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
        if refreshed_session is None:
            logger.warning(
                "MCP {} '{}' could not refresh session for server '{}'",
                capability_kind,
                self._name,
                self._server_name,
            )
            return False
        self._session = refreshed_session
        return True


def _sanitize_name(name: str) -> str:
    """Sanitize an MCP-derived name for model API compatibility."""
    return _SANITIZE_RE.sub("_", re.sub(r"[^a-zA-Z0-9_-]", "_", name))


class MCPToolWrapper(_MCPWrapperBase):
    """将远程mcp调用包装为本地工具接口"""

    _plugin_discoverable = False

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int = 30):
        self._set_mcp_connection(session, server_name)
        self._original_name = tool_def.name
        self._name = _sanitize_name(f"mcp_{server_name}_{tool_def.name}")
        self._description = tool_def.description or tool_def.name
        raw_schema = tool_def.inputSchema or {"type": "object", "properties:": {}}
        self.parameters = self._normalize_schema_for_openai(raw_schema)
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        # from mcp import types

        # retried_transient = False
        # refreshed_session = False

        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(self._original_name, arguments=kwargs),
                    timeout=self._tool_timeout,
                )
            except Exception as e:
                logger.exception(
                    "MCP tool '{}' failed after retry: {}",
                    self._name,
                    type(e).__name__,
                )
                return f"(MCP tool call failed after retry: {type(e).__name__})"

            else:
                parts = []
                for block in result.content:
                    if isinstance(block, type.TextContent):
                        parts.append(block.text)
                    else:
                        parts.append(str(block))
                return "\n".join(parts) or "(no output)"

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


class MCPPromptWrapper(_MCPWrapperBase):
    # 将prompt MCP 包装成bot的tool
    _plugin_discoverable = False

    def __init__(self, session, server_name: str, prompt_def, prompt_timeout: int = 30):
        self._set_mcp_connection(session, server_name)
        self._prompt_name = prompt_def.name
        self._name = f"mcp_{server_name}_prompt_{prompt_def.name}"
        desc = prompt_def.description or prompt_def.name
        self._prompt_timeout = prompt_timeout

        properties: dict[str, Any] = {}
        required: list[str] = []
        for arg in prompt_def.arguments or []:
            prop: dict[str, Any] = {"type": "string"}
            if getattr(arg, "description", None):
                prop["description"] = arg.description
            properties[arg.name] = prop
            if arg.required:
                required.append(arg.name)
        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

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

        retried_transient = False
        refreshed_session = False

        while True:
            try:
                result = await asyncio.wait_for(
                    self._session.get_prompt(
                        self._prompt_name,
                        arguments=kwargs,
                        timeout=self._prompt_timeout,
                    )
                )
                pass
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP prompt '{}' timed out after {}s",
                    self._name,
                    self._prompt_timeout,
                )
                return f"(MCP prompt call timed out after {self._prompt_timeout}s)"
            else:
                parts: list[str] = []
                for message in result.messages:
                    content = message.content
                    if isinstance(content, types.TextContent):
                        parts.append(content.text)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, types.TextContent):
                                parts.append(block.text)
                            else:
                                parts.append(str(block))
                    else:
                        parts.append(str(content))
                return "\n".join(parts) or "(no output)"

            return "(MCP prompt call failed)"


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

        registered_count: int = 0
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
            matched_enabled_tools: set[str] = set()

            for tool_def in tools.tools:
                wrapped_name = f"mcp_{name}_{tool_def.name}"

                wrapper = MCPToolWrapper(
                    session, name, tool_def, tool_timeout=cfg.tool_timeout
                )
                registry.register(wrapper)  # todo impl register
                logger.debug(
                    "MCP: registered tool '{}' from server '{}'", wrapper.name, name
                )
                registered_count += 1
                if enabled_tools:
                    if tool_def.name in enabled_tools:
                        matched_enabled_tools.add(tool_def.name)
                    if wrapped_name in enabled_tools:
                        matched_enabled_tools.add(wrapped_name)

            # get resources
            try:
                resources_result = await session.list_resources()
                for resource in resources_result.resources:
                    wrapper = MCPResourceWrapper(
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
                    wrapper = MCPPromptWrapper(
                        session, name, prompt, prompt_timeout=cfg.tool_timeout
                    )
                    registry.register(wrapper)
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

    server_stacks: dict[str, AsyncExitStack] = {}

    for name, cfg in mcp_server.items():
        try:
            result = await connect_single_server(name, cfg)
        except Exception as e:
            pass

        if result is not None and result[1] is not None:
            server_stacks[result[0]] = result[1]
    return server_stacks

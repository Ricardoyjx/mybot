import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from loguru import logger
from mybot.bus.queue import MessageBus
from mybot.bus.events import InboundMessage, OutboundMessage
from mybot.agent.tools.registry import ToolRegistry
from mybot.config.schema import MCPServerConfig
from mybot.session.manager import Session, SessionManager
from mybot.agent.runner import AgentRunner
from mybot.agent.context import ContextBuilder
from mybot.agent.memory import MemoryStore
from mybot.agent.hook import AgentHook
from mybot.providers.base import LLMProvider
import mybot.agent.context as agent_context
from pathlib import Path

# from mybot.agent import context as agent_context

UNIFIED_SESSION_KEY = "unified:default"


@dataclass
class TurnContext:
    msg: InboundMessage
    session_key: str


class AgentLoop:
    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        model: str | None = None,
        session_manager: SessionManager | None = None,
        tool_registry: ToolRegistry | None = None,
        # tools_config: ToolsConfig | None = None,
        unified_session: bool = False,
        mcp_servers: dict[str, MCPServerConfig] | None = None,
    ):
        self._running = False
        self.bus = bus
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._unified_session = unified_session
        self.provider = provider
        self.model = model
        self.session = session_manager
        self.tool_registry = tool_registry or ToolRegistry()
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        self._mcp_connected = False
        self._mcp_connecting = False

    def _effective_session_key(self, msg: InboundMessage) -> str:
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    async def _run_agent_loop(self):
        pass

    async def run(self) -> None:
        self._running = True
        self._register_default_tools()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Time out error")
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            # raw = msg.content.strip()
            effective_key = self._effective_session_key(msg)

            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(effective_key, []).append(task)

            task.add_done_callback(
                lambda t, k=effective_key: (
                    self._active_tasks.get(k, []) and self._active_tasks[k].remove(t)
                    if t in self._active_tasks.get(k, [])
                    else None
                )
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        pass

    def stop(self) -> None:
        self._running = False
        logger.info("Agent loop stopping")

    async def shutdown(self) -> None:
        """Clean up MCP connections and other resources."""
        for name, stack in list(self._mcp_stacks.items()):
            try:
                await stack.aclose()
            except Exception:
                pass
        self._mcp_stacks.clear()
        self._mcp_connected = False
        logger.info("Agent loop shut down")

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        # tools: ToolRegistry | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        if not self.tool_registry._tools:
            self._register_default_tools()
        await self._connect_mcp()
        msg = InboundMessage(
            sender_id="user",
            chat_id=chat_id,
            content=content,
            channel=channel,
        )

        return await self._process_message(
            msg,
            session_key=session_key,
            # tools=tools,
        )

    async def _process_message(
        self,
        msg: InboundMessage,
        *,
        session_key: str = "default",
        # tools: Any | None = None,
    ) -> OutboundMessage | None:
        session = self._ensure_session(session_key)
        session.add_user_message(msg.content)

        history = list(session.messages)

        runner = AgentRunner(
            provider=self.provider,
            tool_registry=self.tool_registry,
            context_builder=ContextBuilder(
                workspace=self.session.workspace if self.session else Path.cwd()
            ),
            memory_store=MemoryStore(
                workspace=self.session.workspace if self.session else Path.cwd()
            ),
        )
        result = await runner.run(
            user_message=msg.content,
            session_id=session.key,
            hook=AgentHook(),
            history=history,
        )

        if not result:
            return None

        session.add_assistant_message(result)
        if self.session is not None:
            self.session.save(session, fsync=True)

        # 保存到跨会话记忆
        memory = MemoryStore(
            workspace=self.session.workspace if self.session else Path.cwd()
        )
        memory.append_history(
            f"User: {msg.content}\nAssistant: {result}",
            session_key=session_key,
        )

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=result,
        )

    async def _connect_mcp(self) -> None:
        """Connect configured MCP servers."""
        await agent_context.connect_mcp(self, self.tool_registry)

    def _ensure_session(self, session_key: str) -> Session:
        if self.session is not None:
            return self.session.get_or_create(session_key)
        return Session(session_key=session_key)

    def _register_default_tools(self) -> None:
        from mybot.agent.tools.context import ToolContext
        from mybot.agent.tools.loader import ToolLoader

        logger.info("AgentLoop: starting tool registration...")
        ctx = ToolContext()
        logger.debug("AgentLoop: ToolContext created, workspace={}", ctx.workspace)

        loader = ToolLoader()
        registered = loader.load(ctx, self.tool_registry)

        # MyTool needs runtime state reference -- manual registration
        # self.tool_registry.register(MyTool())
        logger.debug("AgentLoop: registered MyTool manually")

        logger.info(
            "AgentLoop: tool registration complete, {} tools: {}",
            len(registered),
            registered,
        )

import asyncio
from loguru import logger
from mybot.bus.queue import MessageBus
from mybot.bus.events import InboundMessage, OutboundMessage
from mybot.agent.tools.registry import ToolRegistry
from mybot.session.manager import Session, SessionManager
from mybot.agent.runner import AgentRunner
from mybot.agent.context import ContextBuilder
from mybot.agent.memory import MemoryStore
from mybot.agent.hook import AgentHook
from mybot.providers.base import LLMProvider
from typing import Any
from mybot.agent import context as agent_context

UNIFIED_SESSION_KEY = "unified:default"


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
    ):
        self._running = False
        self.bus = bus
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._unified_session = unified_session
        self.provider = provider
        self.model = model
        self.session = session_manager
        self.tool_registry = tool_registry or ToolRegistry()

    def _effective_session_key(self, msg: InboundMessage) -> str:
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    async def _run_agent_loop(self):
        pass

    async def run(self) -> None:
        self._running = True
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

            raw = msg.content.strip()
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

    def stop(self) -> None:
        self._running = False
        logger.info("Agent loop stopping")

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        tools: ToolRegistry | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(
            sender_id="user",
            chat_id=chat_id,
            content=content,
            channel=channel,
        )
        return await self._process_message(msg, session_key=session_key)

    async def _process_message(
        self,
        msg: InboundMessage,
        *,
        session_key: str = "default",
        tools: Any | None = None,
    ) -> OutboundMessage | None:
        session = self._ensure_session(session_key)
        session.add_user_message(msg.content)

        history = list(session.messages)

        runner = AgentRunner(
            provider=self.provider,
            tool_registry=self.tool_registry,
            context_builder=ContextBuilder(),
            memory_store=MemoryStore(),
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

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=result,
        )

    async def _connect_mcp(self) -> None:
        """Connect configured MCP servers."""
        # await agent_context.connect_mcp(self, self.tool_registry)  # TODO: MCP 连接暂未实现

    def _ensure_session(self, session_key: str) -> Session:
        if self.session is not None:
            return self.session.get_or_create(session_key)
        return Session(session_key=session_key)

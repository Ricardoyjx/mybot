import asyncio
from loguru import logger
from mybot.bus.queue import MessageBus
from mybot.bus.events import InboundMessage, OutboundMessage
from mybot.agent.tools.registry import ToolRegistry

UNIFIED_SESSION_KEY = "unified:default"


class AgentLoop:
    def __init__(
        self,
        bus: MessageBus,
        providers: str,
        model: str | None = None,
        # session_manager: SessionManager | None = None,
        # tools_config: ToolsConfig | None = None,
        unified_session: bool = False,
    ):
        self._running = False
        self.bus = bus
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._unified_session = unified_session
        self.providers = providers
        self.model = model

    def _effective_session_key(self, msg: InboundMessage) -> str:
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    async def _run_agent_loop():
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
        )
        # Share the dispatch lock so direct calls serialize with bus turns.
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        try:
            async with lock:
                kwargs: dict[str, Any] = {
                    "session_key": session_key,
                }
                if tools is not None:
                    kwargs["tools"] = tools
                return await self._process_message(msg, **kwargs)
        finally:
            await self._runtime_events().run_status_changed(msg, session_key, "idle")
            self._runtime_events.clear_turn(session_key)

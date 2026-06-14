import asyncio
from loguru import logger
from bus.queue import MessageBus
from bus.events import InboundMessage

UNIFIED_SESSION_KEY = "unified:default"

class AgentLoop:
    def __init__(
            self,
            bus: MessageBus,
            providers: str,
            model: str |None = None,
            # session_manager: SessionManager | None = None,
            # tools_config: ToolsConfig | None = None,
            unified_session: bool = False,
    ):
        self._running = False
        self.bus = bus
        self._active_tasks: dict[str,list[asyncio.Task]] = {}  # session_key -> tasks
        self._unified_session = unified_session
        self.providers = providers
        self.model = model
    def _effective_session_key(self,msg: InboundMessage) -> str:
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
                msg = await asyncio.wait_for(self.bus.consume_inbound(),timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Time out error")
                continue
            except Exception as e :
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            effective_key = self._effective_session_key(msg)

            task =asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(effective_key,[]).append(task)

            task.add_done_callback(
                lambda t,k = effective_key: self._active_tasks.get(k,[])
                and self._active_tasks[k].remove(t)
                if t in self._active_tasks.get(k,[])
                else None
            )

    def stop(self) -> None:
        self._running = False
        logger.info("Agent loop stopping")
        
import asyncio
from contextlib import AsyncExitStack, nullcontext
from dataclasses import dataclass
from datetime import time
from loguru import logger
from mybot.agent.cron_turns import CronTurnCoordinator
from mybot.bus.queue import MessageBus
from mybot.bus.events import InboundMessage, OutboundMessage
from mybot.agent.tools.registry import ToolRegistry
from mybot.config.schema import MCPServerConfig, ModelPresetConfig
from mybot.session.manager import Session, SessionManager
from mybot.agent.runner import AgentRunner
from mybot.agent.context import ContextBuilder
from mybot.agent.memory import MemoryStore
from mybot.agent.hook import AgentHook
from mybot.providers.base import LLMProvider
from mybot.providers.factory import ProviderSnapshot
from mybot.session import turn_continuation
import mybot.agent.context as agent_context
from mybot.agent import model_presets as preset_helpers
from pathlib import Path
from mybot.config.schema import ProviderConfig, ToolsConfig
from typing import Callable
from mybot.bus.runtime_events import (
    RuntimeEventBus,
    RuntimeEventPublisher,
    ensure_runtime_event_publisher,
)

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
        unified_session: bool = False,
        mcp_servers: dict[str, MCPServerConfig] | None = None,
        tools_config: ToolsConfig | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        provider_retry_mode: str = "standard",
        tool_hint_max_length: int | None = None,
        restrict_to_workspace: bool = False,
        timezone: str | None = None,
        session_ttl_minutes: int = 0,
        consolidation_ratio: float = 0.5,
        max_messages: int = 120,
        hooks: list[AgentHook] | None = None,
        disabled_skills: list[str] | None = None,
        image_generation_provider_config: ProviderConfig | None = None,
        image_generation_provider_configs: dict[str, ProviderConfig] | None = None,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        model_presets: dict[str, ModelPresetConfig] | None = None,
        model_preset: str | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
        runtime_events: RuntimeEventBus | None = None,
        runtime_model_publisher: Callable[[str, str | None], None] | None = None,
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
        self._concurrency_gate: asyncio.Semaphore | None = asyncio.Semaphore(3)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._pending_queues: dict[str, asyncio.Queue] = {}
        self._cron_turns = CronTurnCoordinator(
            publish_inbound=self.bus.publish_inbound,
            dispatch=self._dispatch,
            is_running=lambda: self._running,
        )

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

    def _runtime_events(self) -> RuntimeEventPublisher:
        return ensure_runtime_event_publisher(self)

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent.
        同一 session 内串行处理（保证消息顺序），不同 session 间并发处理（提高吞吐）。"""

        # session 路由和锁 准备
        session_key = msg.session_key

        # _session_locks.setdefault：每个 session 一把 asyncio.Lock，
        # 保证同一 session 同时只有一个 _dispatch 在执行。setdefault 实现懒创建 + 复用。
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())

        # 限制同时处理的不同 session 数量。如果设为 <=0 则为 None，用 nullcontext() 代替（无限并发）。
        gate = self._concurrency_gate or nullcontext()

        pending: asyncio.Queue | None = None
        try:
            async with lock, gate:
                pending = asyncio.Queue(maxsize=20)
                self._pending_queues[session_key] = pending
                try:
                    on_stream = on_stream_end = None
                    # 默认启用流式输出，除非 metadata 显式设置 _wants_stream=False
                    wants_stream = (
                        msg.metadata.get("_wants_stream", True)
                        if msg.metadata
                        else True
                    )
                    if wants_stream:
                        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                        stream_segment = 0

                        def _current_stream_id() -> str:
                            return f"{stream_base_id}:{stream_segment}"

                        # 发布 OutboundMessage 带 _stream_delta=True 和当前 _stream_id，让前端实时渲染
                        async def on_stream(delta: str) -> None:
                            meta = dict(msg.metadata or {})
                            meta["_stream_delta"] = True
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content=delta,
                                    metadata=meta,
                                )
                            )

                        # 发布带 _stream_end=True 的空消息。resuming=True
                        # 表示 agent 还在执行（如要调用工具），前端知道连接没断
                        async def on_stream_end(*, resuming: bool = False) -> None:
                            nonlocal stream_segment
                            meta = dict(msg.metadata or {})
                            meta["_stream_end"] = True
                            meta["_resuming"] = resuming
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    content="",
                                    metadata=meta,
                                )
                            )
                            stream_segment += 1

                    response = await self._process_message(
                        msg,
                        on_stream=on_stream,
                        on_stream_end=on_stream_end,
                        pending_queue=pending,
                    )
                    completed_channel = msg.channel
                    completed_chat_id = msg.chat_id

                    if response is not None:
                        await self.bus.publish_outbound(response)
                        completed_channel = response.channel
                        completed_chat_id = response.chat_id
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata=msg.metadata or {},
                            )
                        )
                    continuing = turn_continuation.internal_continuation_pending(
                        msg.metadata
                    )
                    if not continuing:
                        await self._runtime_events().turn_completed(
                            channel=completed_channel,
                            chat_id=completed_chat_id,
                            session_key=session_key,
                            metadata=msg.metadata,
                        )
                    self._cron_turns.complete(msg, response=response)

                except asyncio.CancelledError:
                    self._cron_turns.complete(
                        msg,
                        error=asyncio.CancelledError(),
                    )
                    logger.info("Task cancelled for session {}", session_key)
                    # Preserve partial context from the interrupted turn so
                    # the user does not lose tool results and assistant
                    # messages accumulated before /stop.  The checkpoint was
                    # already persisted to session metadata by
                    # _emit_checkpoint during tool execution; materializing
                    # it into session history now makes it visible in the
                    # next conversation turn.
                    try:
                        key = self._effective_session_key(msg)
                        session = self.sessions.get_or_create(key)
                        if self._restore_runtime_checkpoint(session):
                            self._clear_pending_user_turn(session)
                            self.sessions.save(session)
                            logger.info(
                                "Restored partial context for cancelled session {}",
                                key,
                            )
                    except Exception:
                        logger.debug(
                            "Could not restore checkpoint for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    raise
                except Exception as exc:
                    logger.exception(
                        "Error processing message for session {}", session_key
                    )
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="Sorry, I encountered an error.",
                        )
                    )
                    if not turn_continuation.internal_continuation_pending(
                        msg.metadata
                    ):
                        await self._runtime_events().turn_completed(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            session_key=session_key,
                            metadata=msg.metadata,
                        )
                    self._cron_turns.complete(msg, error=exc)

                finally:
                    # Drain any messages still in the pending queue and re-publish
                    # them to the bus so they are processed as fresh inbound messages
                    # rather than silently lost.  Only remove our own queue; a
                    # later task waiting on the lock must not be able to steal
                    # cleanup ownership.
                    queue = None
                    if self._pending_queues.get(session_key) is pending:
                        queue = self._pending_queues.pop(session_key, None)
                    else:
                        queue = pending
                    if queue is not None:
                        leftover = 0
                        while True:
                            try:
                                item = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            await self.bus.publish_outbound(item)
                            leftover += 1
                        if leftover:
                            logger.info(
                                "Re-published {} leftover message(s) to bus for session {}",
                                leftover,
                                session_key,
                            )
                    if not turn_continuation.internal_continuation_pending(
                        msg.metadata
                    ):
                        await self._runtime_events().run_status_changed(
                            msg, session_key, "idle"
                        )
                        self._runtime_events().clear_turn(session_key)
                    await self._cron_turns.publish_next_deferred(session_key)
        finally:
            if pending is None:
                await self._runtime_events().run_status_changed(
                    msg, session_key, "idle"
                )
                self._runtime_events().clear_turn(session_key)
                await self._cron_turns.publish_next_deferred(session_key)

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
        on_stream: Callable | None = None,
        on_stream_end: Callable | None = None,
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
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )

    async def _process_message(
        self,
        msg: InboundMessage,
        *,
        session_key: str = "default",
        on_stream: Callable | None = None,
        on_stream_end: Callable | None = None,
        pending_queue: asyncio.Queue | None = None,
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
            on_stream=on_stream,
            on_stream_end=on_stream_end,
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

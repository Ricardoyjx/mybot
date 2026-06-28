import asyncio
from curses.ascii import SUB
from dataclasses import dataclass, field
import json
from pathlib import Path
from re import A
import time
from tkinter import NO
from typing import Any, Callable
import uuid

from loguru import logger
from openpyxl import Workbook
from mybot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from mybot.agent.runner import AgentRunSpec, AgentRunner
from mybot.agent.tools.context import ToolContext
from mybot.agent.tools.loader import ToolLoader
from mybot.agent.tools.registry import ToolRegistry
from mybot.bus.queue import MessageBus
from mybot.config.schema import ToolsConfig
from mybot.providers.base import LLMProvider
from mybot.utils.prompt_templates import render_template


@dataclass(slots=True)
class SubagentStatus:

    task_id: str
    label: str
    task_description: str
    started_at: float
    phase: str = "initalizing"
    iteration: int = 0
    tool_events: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    stop_reason: str | None = None
    error: str | None = None


class _SubagentHook(AgentHook):
    def __init__(self, task_id: str, status: SubagentStatus | None = None):

        super().__init__()
        self.task_id = task_id
        self.status = status or SubagentStatus()

    async def before_run(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing :{} with args:{}",
                self.task_id,
                tool_call.name,
                args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self.status.iteration = context.iteration
        self.status.tool_events = list(context.tool_events)
        self.status.usage = dict(context.usage)
        if context.error:
            self.status.error = str(context.error)


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        llm_wall_timeout_for_session: (
            Callable[[str | None], float | None] | None
        ) = None,
    ):
        # defaults = AgentDefaults()
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.tools_config = tools_config or ToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.max_iterations = max_iterations if max_iterations is not None else 5
        self.max_concurrent_subagents = (
            max_concurrent_subagents if max_concurrent_subagents is not None else 3
        )
        self.runner = AgentRunner(provider)
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}

    def _subagent_tools_config(self) -> ToolsConfig:
        return ToolsConfig(
            exec=self.tools_config.exec,
            web=self.tools_config.web,
            file=self.tools_config.file,
            restrict_to_workspace=self.restrict_to_workspace,
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
    ) -> ToolRegistry:
        root = self.workspace if workspace is None else workspace
        cfg = (
            tools_config if tools_config is not None else self._subagent_tools_config()
        )
        ctx = ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            # file_state_store=FileStates(),
            # workspace_sandbox=workspace_sandbox_status(
            #     restrict_to_workspace=cfg.restrict_to_workspace,
            #     workspace=root,
            # ),
            strict_to_workspace=cfg.restrict_to_workspace,
        )
        tool_registry = ToolRegistry()
        ToolLoader.load(ctx, tool_registry, scope="subagent")
        return tool_registry

    def set_provider(self, provider: LLMProvider, model: str | None = None):
        self.provider = provider
        self.model = model
        self.runner.provider = provider

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: Path | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
            session_key: session_key,
        }

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                task,
                display_label,
                origin,
                origin_message_id,
                temperature,
                workspace_scope,
            )
        )
        self._running_tasks[task_id] = bg_task

        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str | None],
        status: SubagentStatus,
        origin_message_id: str | None,
        temperature: float | None,
        workspace_scope: Path | None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        try:
            root = self.workspace if workspace_scope is not None else self.workspace
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            tools = self._build_tools(workspace=root, tools_config=cfg)
            system_prompt = self._build_subagent_prompt(workspace=cfg)
            messages: list[dict[str, Any]] = [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": task,
                },
            ]

            sess_key = origin.get("session_key")
            llm_timeout = (
                self._llm_wall_timeout_for_session(sess_key)
                if self.self._llm_wall_timeout_for_session
                else None
            )
            # token = (
            #     bind_workspace_scope(workspace_scope)
            #     if workspace_scope is not None
            #     else None
            # )
            # try:
            result = await self.runner.run(
                AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    model=self.model,
                    temperature=temperature,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=_SubagentHook(task_id, status),
                    max_iterations_message="Task completed but no final response was generated.",
                    finalize_on_max_iterations=False,
                    error_message=None,
                    fail_on_tool_error=True,
                    checkpoint_callback=_on_checkpoint,
                    session_key=sess_key,
                    workspace=root,
                    llm_timeout_s=llm_timeout,
                )
            )
            # finally:
            # if token is not None:
            #     reset_workspace_scope(token)
            status.phase = "done"
            status.stop_reason = result.stop_reason
            if result.stop_reason == "tool_error":
                status.tool_events = list(result.tool_events)
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    self._format_partial_progress(result),
                    origin,
                    "error",
                    origin_message_id,
                )
            elif result.stop_reason == "error":
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    result.error or "Error: subagent execution failed.",
                    origin,
                    "error",
                    origin_message_id,
                )
            else:
                final_result = (
                    result.final_content
                    or "Task completed but no final response was generated."
                )
                logger.info("Subagent [{}] completed successfully", task_id)
                await self._announce_result(
                    task_id, label, task, final_result, origin, "ok", origin_message_id
                )

        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.exception("Subagent [{}] failed", task_id)
            await self._announce_result(
                task_id, label, task, f"Error: {e}", origin, "error", origin_message_id
            )

    def _build_subagent_prompt(self, workspace: Path | None = None) -> str:
        """Build a focused system prompt for the subagent."""
        from mybot.agent.context import ContextBuilder
        from mybot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        root = workspace or self.workspace
        skills_summary = SkillsLoader(
            root,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            workspace=str(root),
            skills_summary=skills_summary or "",
        )

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        origin_message_id: str | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        pass

    @staticmethod
    def _format_partial_progress(result) -> str:
        pass

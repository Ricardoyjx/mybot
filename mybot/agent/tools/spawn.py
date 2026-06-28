from contextvars import ContextVar
from typing import Any

from openpyxl import Workbook

from mybot.agent.tools.base import Tool
from mybot.agent.subagent import SubagentManager
from mybot.agent.tools.base import tool_parameters
from mybot.agent.tools.schema import NumberSchema, StringSchema, tool_parameters_schema
from mybot.agent.tools.context import ContextAware, RequestContext


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema(),
        label=StringSchema(),
        temperature=NumberSchema(
            description=(""),
            minimum=0,
            maximum=1,
        ),
        required=["task"],
    )
)
class SpawnTool:
    """Spawn a new agent."""

    def __init__(self, manager: "SubagentManager"):
        self.manager = manager
        self._origin_channel = ContextVar[str] = ContextVar(
            "spawn_origin_channel", default="cli"
        )
        self._origin_chat_id = ContextVar[str] = ContextVar(
            "spawn_origin_chat_id", default="direct"
        )
        self._session_key = ContextVar[str] = ContextVar(
            "spawn_session_key", default="cli:direct"
        )
        self._origin_message_id: ContextVar[str | None] = ContextVar(
            "spawn_origin_message_id", default=None
        )

        @classmethod
        def create(cls, ctx: Any) -> Tool:
            return cls(manager=ctx.subagent_manager)

        def set_context(self, ctx: RequestContext) -> None:
            self._origin_channel.set(ctx.channel)
            self._origin_chat_id.set(ctx.chat_id)
            self._session_key.set(ctx.session_key or f"{ctx.channel}:{ctx.chat_id}")
            self._origin_message_id.set(ctx.message_id)

        @property
        def name(self) -> str:
            return "spawn"

        @property
        def description(self) -> str:
            return (
                "Spawn a subagent to handle a task in the background. "
                "Use this for complex or time-consuming tasks that can run independently. "
                "The subagent will complete the task and report back when done. "
                "For deliverables or existing projects, inspect the workspace first "
                "and use a dedicated subdirectory when helpful."
            )

        async def execute(
            self, task: str, label: str, temperature: float | None = None, **kwargs: Any
        ) -> str:
            running = self._manager.get_running_count()
            limit = self._manager.get_max_concurrent_subagents
            if running >= limit:
                return f"Maximum concurrent subagents reached ({limit}). Please try again later."
            return await self._manager.spawn(
                task=task,
                label=label,
                temperature=temperature,
                origin_channel=self._origin_channel.get(),
                origin_chat_id=self._origin_chat_id.get(),
                session_key=self._session_key.get(),
                origin_message_id=self._origin_message_id.get(),
                # workspace_scope=current_workspace_scope(),
            )

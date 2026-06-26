from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentHookContext:
    iteration: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""


@dataclass(slots=True)
class AgentRunHookContext:
    pass


class AgentHook:
    """Minimal lifecycle surface for shared runner customization."""

    def __init__(self, reraise: bool = False) -> None:
        self._reraise = reraise

    def wants_streaming(self) -> bool:
        return False

    async def before_run(self, context: AgentRunHookContext) -> None:
        pass

    async def after_run(self, context: AgentRunHookContext) -> None:
        pass

    async def on_error(self, context: AgentRunHookContext) -> None:
        pass

    async def on_finally(self, context: AgentRunHookContext) -> None:
        pass

    async def before_iteration(self, context: AgentHookContext) -> None:
        pass

    async def _before_iteration(self, context: AgentHookContext) -> None:
        await self.before_iteration(context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        pass

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        pass

    async def before_execute_tools(self, context: AgentHookContext | None = None) -> None:
        pass

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        pass

    async def emit_reasoning_end(self) -> None:
        pass

    async def after_iteration(self, context: AgentHookContext, messages: list | None = None) -> None:
        pass

    def finalize_content(
        self, context: AgentHookContext, content: str | None
    ) -> str | None:
        return content


class SDKCaptureHook(AgentHook):
    def __init__(self) -> None:
        super().__init__()
        self.tools_used: list[str] = []
        self.message: list[dict[str, Any]] = []

from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentHookContext:
    pass


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

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        pass

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        pass

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        pass

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        pass

    async def emit_reasoning_end(self) -> None:
        """Mark the end of an in-flight reasoning stream.

        Hooks that buffer ``emit_reasoning`` chunks (for in-place UI updates)
        flush and freeze the rendered group here. One-shot hooks ignore.
        """
        pass

    async def after_iteration(self, context: AgentHookContext) -> None:
        pass

    def finalize_content(
        self, context: AgentHookContext, content: str | None
    ) -> str | None:
        return content


class SDKCaptureHook(AgentHook):
    pass

from dataclasses import dataclass
from typing import Any
from mybot.agent.loop import AgentLoop
from mybot.agent.hook import AgentHook, SDKCaptureHook


@dataclass(slots=True)
class RunResult:
    content: str
    tools_used: list[str]
    message: list[dict[str, Any]]


class Mybot:

    def __init__(self, loop: AgentLoop):
        self._loop = loop

    async def run(
        self,
        message: str,
        *,
        session_key: str = "cli:direct",
        hooks: list[AgentHook] | None = None,
    ) -> RunResult:
        """Run the agent once and return the result.

        Args:
            message: The user message to process.
            session_key: Session identifier for conversation isolation.
                Different keys get independent history.
            hooks: Optional lifecycle hooks for this run.
        """
        capture = SDKCaptureHook()
        prev = self._loop._extra_hooks
        base_hooks = list(hooks) if hooks is not None else list(prev or [])
        self._loop._extra_hooks = [capture, *base_hooks]
        try:
            response = await self._loop.process_direct(
                message,
                session_key=session_key,
            )
        finally:
            self._loop._extra_hooks = prev

        content = (response.content if response else None) or ""
        return RunResult(
            content=content,
            tools_used=capture.tools_used,
            message=capture.message,
        )

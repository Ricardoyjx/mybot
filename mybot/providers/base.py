from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class LLMResponse:
    """LLM 返回的统一结构。"""
    content: str = ""
    tool_calls: list[Any] = field(default_factory=list)


class LLMProvider(ABC):

    @abstractmethod
    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[[], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        ...

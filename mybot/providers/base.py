from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
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
    ) -> LLMResponse: ...


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""

    id: str
    name: str
    arguments: Any
    extra_content: dict[str, Any] | None = None
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def to_openai_tool_call(self) -> dict[str, Any]:
        """Serialize to an OpenAI-style tool_call payload."""
        arguments = (
            self.arguments
            if isinstance(self.arguments, str)
            else json.dumps(self.arguments, ensure_ascii=False)
        )
        tool_call = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": arguments,
            },
        }
        if self.extra_content:
            tool_call["extra_content"] = self.extra_content
        if self.provider_specific_fields:
            tool_call["provider_specific_fields"] = self.provider_specific_fields
        if self.function_provider_specific_fields:
            tool_call["function"][
                "provider_specific_fields"
            ] = self.function_provider_specific_fields
        return tool_call

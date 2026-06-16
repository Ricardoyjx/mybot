from mybot.providers.base import LLMProvider, LLMResponse
from typing import Any


class StubProvider(LLMProvider):
    """占位 provider，返回固定提示。替换为真实实现后即可正常使用。"""

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            content="[stub] 当前使用占位 provider，请接入真实 LLM 实现。",
            tool_calls=[],
        )

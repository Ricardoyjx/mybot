"""OpenAI-compatible LLM provider.

支持 OpenAI API 以及任何兼容接口（如 Azure、本地代理等）。
通过环境变量配置：
    OPENAI_API_KEY   - API 密钥
    OPENAI_BASE_URL  - 自定义接口地址（可选）
    OPENAI_MODEL     - 默认模型（可选，默认 gpt-4o-mini）
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, RateLimitError

from mybot.providers.base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._max_retries = max_retries
        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=base_url or os.getenv("OPENAI_BASE_URL"),
        )

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """调用 OpenAI Chat Completions API，带重试。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._client.chat.completions.create(**kwargs)
                choice = resp.choices[0]
                msg = choice.message

                tool_calls = []
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append(
                            type("ToolCall", (), {
                                "id": tc.id,
                                "type": "function",
                                "function": type("Fn", (), {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                })(),
                            })()
                        )

                return LLMResponse(
                    content=msg.content or "",
                    tool_calls=tool_calls,
                )

            except RateLimitError as e:
                logger.warning("速率限制，第 {}/{} 次重试: {}", attempt, self._max_retries, e)
                last_error = e
            except APIConnectionError as e:
                logger.warning("连接失败，第 {}/{} 次重试: {}", attempt, self._max_retries, e)
                last_error = e
            except APIStatusError as e:
                if e.status_code >= 500:
                    logger.warning("服务端错误 {}，第 {}/{} 次重试", e.status_code, attempt, self._max_retries)
                    last_error = e
                else:
                    raise

        raise RuntimeError(f"LLM 调用失败（已重试 {self._max_retries} 次）: {last_error}")

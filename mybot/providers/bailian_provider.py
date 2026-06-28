"""DeepSeek LLM provider。

支持 DeepSeek API（OpenAI 兼容格式）。
通过环境变量配置：
    DEEPSEEK_API_KEY   - API 密钥（必须）
    DEEPSEEK_BASE_URL  - 自定义接口地址（可选）
    DEEPSEEK_MODEL     - 模型名称（可选，默认 qwen3.7-plus）
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

from loguru import logger
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, RateLimitError

from mybot.providers.base import LLMProvider, LLMResponse


class BailianProvider(LLMProvider):
    """DeepSeek API 提供商，兼容 OpenAI Chat Completions 格式。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.model = model or os.getenv("QWEN_MODEL", "qwen3.7-plus")
        self._max_retries = max_retries
        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            base_url=base_url
            or os.getenv(
                "DEEPSEEK_BASE_URL",
                "https://ws-1agzecahhmlzxv9e.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            ),
        )

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[[], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """调用 DeepSeek Chat Completions API，支持流式和重试。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        use_stream = on_stream is not None

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                if use_stream:
                    return await self._stream_call(kwargs, on_stream, on_stream_end)
                return await self._normal_call(kwargs)

            except RateLimitError as e:
                logger.warning(
                    "速率限制，第 {}/{} 次重试: {}", attempt, self._max_retries, e
                )
                last_error = e
            except APIConnectionError as e:
                logger.warning(
                    "连接失败，第 {}/{} 次重试: {}", attempt, self._max_retries, e
                )
                last_error = e
            except APIStatusError as e:
                if e.status_code >= 500:
                    logger.warning(
                        "服务端错误 {}，第 {}/{} 次重试",
                        e.status_code,
                        attempt,
                        self._max_retries,
                    )
                    last_error = e
                else:
                    raise

        raise RuntimeError(
            f"LLM 调用失败（已重试 {self._max_retries} 次）: {last_error}"
        )

    async def _normal_call(self, kwargs: dict[str, Any]) -> LLMResponse:
        """非流式调用。"""
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls = self._parse_tool_calls(msg)
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
        )

    async def _stream_call(
        self,
        kwargs: dict[str, Any],
        on_stream: Callable[[str], Awaitable[None]],
        on_stream_end: Callable[[], Awaitable[None]] | None,
    ) -> LLMResponse:
        """流式调用：逐 chunk 推送给回调，最终返回完整结果。"""
        kwargs["stream"] = True
        stream = await self._client.chat.completions.create(**kwargs)

        full_content = ""
        tool_call_deltas: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            delta = choice.delta

            if delta.content:
                full_content += delta.content
                if on_stream:
                    await on_stream(delta.content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_deltas:
                        tool_call_deltas[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    entry = tool_call_deltas[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

            if choice.finish_reason is not None:
                break

        if on_stream_end:
            await on_stream_end()

        tool_calls = []
        for idx in sorted(tool_call_deltas.keys()):
            entry = tool_call_deltas[idx]
            tool_calls.append(
                type(
                    "ToolCall",
                    (),
                    {
                        "id": entry["id"],
                        "type": "function",
                        "function": type(
                            "Fn",
                            (),
                            {
                                "name": entry["name"],
                                "arguments": entry["arguments"],
                            },
                        )(),
                    },
                )()
            )

        return LLMResponse(
            content=full_content,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _parse_tool_calls(msg: Any) -> list[Any]:
        """从非流式响应中解析 tool_calls。"""
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    type(
                        "ToolCall",
                        (),
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": type(
                                "Fn",
                                (),
                                {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            )(),
                        },
                    )()
                )
        return tool_calls

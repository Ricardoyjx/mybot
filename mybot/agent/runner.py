from typing import Any, Awaitable, Callable

from mybot.providers.base import LLMProvider
from mybot.agent.tools.registry import ToolRegistry
from mybot.agent.context import ContextBuilder
from mybot.agent.memory import MemoryStore
from mybot.agent.hook import AgentHook, AgentHookContext
from loguru import logger

_TOOL_RESULT_MAX_CHARS = 16000


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        context_builder: ContextBuilder,
        memory_store: MemoryStore,
        max_iteration: int = 10,
        is_subagent: bool = False,
    ):
        self.provider = provider
        self.tool_registry = tool_registry
        self.context_builder = context_builder
        self.memory_store = memory_store
        self.max_iteration = max_iteration
        self.is_subagent = is_subagent

        if is_subagent:
            self.max_iteration = min(max_iteration, 15)

    async def run(
        self,
        user_message: str,
        session_id: str,
        hook: AgentHook,
        history: list[dict[str, str]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[[], Awaitable[None]] | None = None,
        on_status: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """完整的 ReAct 循环，支持流式输出。"""

        # 构建上下文消息
        messages = self.context_builder.build_messages(
            current_message=user_message,
            session_key=session_id,
            history=history,
        )

        # 获取可用工具 schema
        tools = self.tool_registry.get_tool_schemas()
        logger.debug("Tools sent to LLM: {}", tools)

        final_response = ""
        tool_calls_history = []

        async def _status(text: str) -> None:
            if on_status:
                try:
                    await on_status(text)
                except Exception:
                    pass

        for iteration in range(self.max_iteration):

            context = AgentHookContext(
                iteration=iteration,
                messages=messages,
                session_id=session_id,
            )

            # 生命周期钩子：迭代前
            await hook._before_iteration(context)

            # 每轮都传流式回调，provider 内部决定是否 stream
            await _status("thinking")
            response = await self.provider.chat_with_retry(
                messages=messages,
                tools=tools if tools else None,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
            )

            # 检查是否需要调用工具
            if not response.tool_calls:
                final_response = response.content
                break

            serializable_calls = []
            for tc in response.tool_calls:
                serializable_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )

            # 将 assistant 消息（含 tool_calls）加入上下文
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": serializable_calls,
                }
            )

            # 生命周期钩子：执行工具前
            await hook.before_execute_tools()

            # 逐个执行工具
            for tool_call in response.tool_calls:
                await _status(f"calling:{tool_call.function.name}")
                result = await self._execute_tool(tool_call)

                if len(result) > _TOOL_RESULT_MAX_CHARS:
                    result = result[:_TOOL_RESULT_MAX_CHARS] + "\n...(truncated)"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

                tool_calls_history.append(tool_call)

            # 生命周期钩子：迭代后
            await hook.after_iteration(iteration, messages)



        else:
            final_response = ""

        return final_response

    async def _execute_tool(self, tool_call) -> str:
        import json

        tool_name = tool_call.function.name
        tool_args = json.loads(tool_call.function.arguments)

        if tool_name == "save_memory":
            content = tool_args.get("content", "")
            await self.memory_store.write_memory(content)
            return "Memory saved successfully."

        # 普通工具调用
        try:
            result = await self.tool_registry.execute(tool_name, tool_args)
            return str(result)
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from mybot.providers.base import LLMProvider
from mybot.agent.tools.registry import ToolRegistry
from mybot.agent.context import ContextBuilder
from mybot.agent.memory import MemoryStore
from mybot.agent.hook import AgentHook, AgentHookContext
from loguru import logger

_TOOL_RESULT_MAX_CHARS = 16000

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
_ARREARAGE_ERROR_MESSAGE = (
    "The AI provider rejected the request because the API key is out of quota or the "
    "account is in arrears. Please top up / check the billing status of your API key and try again."
)
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"
_MAX_EMPTY_RETRIES = 2
_MAX_LENGTH_RECOVERIES = 3
_MAX_INJECTIONS_PER_TURN = 3
_MAX_INJECTION_CYCLES = 5
_SNIP_SAFETY_BUFFER = 1024
_MICROCOMPACT_KEEP_RECENT = 10
_MICROCOMPACT_MIN_CHARS = 500
_COMPACTABLE_TOOLS = frozenset(
    {
        "read_file",
        "exec",
        "grep",
        "find_files",
        "web_search",
        "web_fetch",
        "list_dir",
        "list_exec_sessions",
    }
)
# read_file is the recovery path for persisted results; exempting it prevents persist->read->persist loops.
_TOOL_RESULT_OFFLOAD_EXEMPT_TOOLS = frozenset({"read_file"})
_BACKFILL_CONTENT = "[Tool result unavailable — call was interrupted or lost]"

# Backward-compatible module attribute for tests/extensions that monkeypatch
# the former single-file tracker hook. Runtime uses prepare_file_edit_trackers.
# prepare_file_edit_tracker = _prepare_file_edit_tracker


@dataclass(slots=True)
class AgentRunSpec:
    """Configuration for a single agent execution."""

    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    max_iterations: int
    max_tool_result_chars: int
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    hook: AgentHook | None = None
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    max_iterations_message: str | None = None
    concurrent_tools: bool = False
    fail_on_tool_error: bool = False
    workspace: Path | None = None
    session_key: str | None = None
    context_window_tokens: int | None = None
    context_block_limit: int | None = None
    provider_retry_mode: str = "standard"
    progress_callback: Any | None = None
    stream_progress_deltas: bool = True
    retry_wait_callback: Any | None = None
    checkpoint_callback: Any | None = None
    injection_callback: Any | None = None
    llm_timeout_s: float | None = None
    goal_active_predicate: Callable[[], bool] | None = None
    goal_continue_message: str | None = None
    finalize_on_max_iterations: bool = True


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        context_builder: ContextBuilder,
        memory_store: MemoryStore,
        max_iteration: int = 5,
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
        skill_names: list[str] | None = None,
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
            skill_names=skill_names,
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
                session_key=session_id,
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
            # 迭代用完了，强制 LLM 根据已有信息直接回答
            logger.warning(
                "AgentRunner: max iterations ({}) reached, forcing final answer",
                self.max_iteration,
            )
            await _status("thinking")
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "你已经用完了所有工具调用次数。"
                        "请立刻根据上面已有的信息直接回答用户的问题，"
                        "不要再调用任何工具。"
                    ),
                }
            )
            try:
                resp = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=None,
                    on_stream=on_stream,
                    on_stream_end=on_stream_end,
                )
                final_response = resp.content or "抱歉，我无法在限定步骤内找到答案。"
            except Exception as e:
                logger.error("Forced final answer failed: {}", e)
                final_response = "抱歉，我无法在限定步骤内找到答案。"

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

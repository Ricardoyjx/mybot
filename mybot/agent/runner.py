_TOOL_RESULT_MAX_CHARS = 16000

from mybot.providers.base import LLMProvider
from mybot.agent.tools.registry import ToolRegistry
from mybot.agent.context import ContextBuilder
from mybot.agent.memory import MemoryStore
from mybot.agent.hook import AgentHook, AgentHookContext


class AgentRunner:
    def __init__(
        self,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        context_builder: ContextBuilder,
        memory_store: MemoryStore,
        max_iteration: int = 40,
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
    ) -> str:
        """完整的ReAct 循环"""

        # init message queue
        messages = self.context_builder.build_messages(
            user_message=user_message,
            session_id=session_id,
            history=history,
        )

        # get reachable tools
        tools = self.tool_registry.get_tool_schemas()

        final_response = ""
        tool_calls_history = []

        for iteration in range(self.max_iteration):

            context = AgentHookContext(
                iteration=iteration,
                messages=messages,
                session_id=session_id,
            )

            # lifecycle hook: before iteration
            # await self._before_iteration(iteration, messages)  # todo impl
            await hook._before_iteration(context)
            # call LLM
            response = await self.provider.chat_with_retry(  # todo impl
                messages=messages,
                tools=tools if tools else None,
            )

            # check if need to call tools
            if not response.tool_calls:
                final_response = response.content
                break

            # need to call tools
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                }
            )
            # lifecycle hook before execute tools
            # await self._before_execute_tools(response.tool_calls)
            await hook.before_execute_tools()
            # call tools one by one
            for tool_call in response.tool_calls:
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

            # lifecycle hook after iteration
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
            await self.memory_store.save_memory(content)
            return "Memory saved successfully."

        # normal tool calls
        try:
            result = await self.tool_registry.execute(tool_name, tool_args)
            return str(result)
        except Exception as e:
            return f"Error executing {tool_name}:{str(e)}"

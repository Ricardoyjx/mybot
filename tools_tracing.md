# tools 流程图

``` python
┌─────────────────────────────────────────────────────┐
│              主循环 (max_iterations轮)               │
│                                                     │
│  ① get_definitions()  → 发给 LLM（含工具 JSON Schema）│
│                                                      │
│  ② LLM 返回 tool_calls（如 read_file(path="x.py")）  │
│                                                      │
│  ③ _execute_tools()                                 │
│     ├─ _partition_tool_batches()  分批（并发/串行）     │
│     └─_run_tool()  对每个 tool_call：                │
│         ├─ prepare_call():  查找 + 类型转换 + 校验     │
│         └─ tool.execute(**params):  实际执行           │
│                                                      │
│  ④ 结果包装成 {"role":"tool", ...} 追加到 messages    │
│                                                      │
│  ⑤ continue → 回到 ①，再次请求 LLM                   │
│                                                     │
│  直到 LLM 不再返回 tool_calls → 退出循环              │
└─────────────────────────────────────────────────────┘
```

## Tools 调用的完整链路

整个流程分 三个阶段：告诉 LLM 有哪些工具 → LLM 决定调用哪个工具 → 实际执行工具并返回结果。

## 阶段一：告诉 LLM 有哪些工具（Schema 注入）

AgentRunner._run_core 的主循环中，每次请求 LLM 前：

```python
# runner.py 第 730 行
kwargs = self._build_request_kwargs(
    spec,
    messages,
    tools=spec.tools.get_definitions(),   # ← 这里
)
```

`spec.tools.get_definitions()` 遍历 registry 里所有已注册的 Tool 对象，调用每个工具的 `to_schema()`：

```python
# base.py: to_schema()
{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file...",
        "parameters": {"type": "object", "properties": {...}, "required": [...]},
    }
}
```

这些 JSON Schema 被作为 tools 参数发给 LLM（OpenAI/Anthropic 格式），这样 LLM 就知道有哪些工具可用、参数是什么。

## 阶段二：LLM 返回 tool_call，进入执行循环

LLM 返回响应后，`_run_core` 判断是否需要执行工具：

```python
# runner.py 第 396 行
if response.should_execute_tools:
    assistant_message = build_assistant_message(...)   # 先把 LLM 的回复存入 messages
    messages.append(assistant_message)

    results, new_events, fatal_error = await self._execute_tools(
        spec,
        response.tool_calls,         # LLM 要调用的工具列表
        external_lookup_counts,
        workspace_violation_counts,
    )
```

这是一个循环（最多 max_iterations 轮）：

LLM 返回 tool_calls → 执行工具 → 结果追加到 messages → 再次请求 LLM → ...

直到 LLM 返回纯文本（不再调用工具），循环结束。

## 阶段三：实际执行工具

### 3a. 分批并行 _execute_tools

```python
# runner.py 第 997 行
batches = self._partition_tool_batches(spec, tool_calls)   # 按并发安全性分批
for batch in batches:
    if spec.concurrent_tools and len(batch) > 1:
        # 只读工具可以并发执行
        batch_results = await asyncio.gather(*(
            self._run_tool(spec, tc, ...) for tc in batch
        ))
    else:
        # 有副作用的工具串行执行
        for tool_call in batch:
            result = await self._run_tool(spec, tc, ...)
```

### 3b. 单个工具执行 _run_tool

这是核心执行路径，有 两条分支：

```python
# runner.py 第 1060-1118 行
prepare_call = getattr(spec.tools, "prepare_call", None)
tool, params, prep_error = None, tool_call.arguments, None

if callable(prepare_call):
    prepared = prepare_call(tool_call.name, tool_call.arguments)
    tool, params, prep_error = prepared   # 解析、校验、类型转换

if prep_error:
    return prep_error + hint, event, None   # 校验失败，直接返回错误

# 实际执行
if tool is not None:
    result = await tool.execute(**params)    # ← 直接调用 Tool 实例
else:
    result = await spec.tools.execute(name, params)  # ← 通过 Registry 调用
```

两条路径的区别：

| 路径                               | 触发条件                          | 说明                                                         |
|------------------------------------|-----------------------------------|--------------------------------------------------------------|
| `tool.execute(**params)`           | prepare_call 成功解析出 Tool 实例 | 快速路径，跳过 Registry 二次查找                             |
| `spec.tools.execute(name, params)` | prepare_call 不可用或未返回 tool  | 走 Registry 的 execute，内部再调 prepare_call + tool.execute |

### 3c. ToolRegistry.execute 做了什么

```python
# registry.py 第 157 行
async def execute(self, name, params):
    tool, params, error = self.prepare_call(name, params)  # 查找 + 校验
    if error:
        return error + hint
    result = await tool.execute(**params)   # 调用具体工具
    return result
```

`prepare_call` 做三件事：

- **查找工具**：`self._tools.get(name)`，找不到还会模糊匹配建议
- **参数强制类型转换**：`tool.cast_params(params)` — 按 JSON Schema 做安全类型转换
- **参数校验**：`tool.validate_params(cast_params)` — 必填字段、类型等

### 3d. 具体工具的 execute

以 ReadFileTool 为例：

```python
class ReadFileTool(Tool):
    name = "read_file"
    
    async def execute(self, path=None, offset=1, limit=None, **kwargs):
        fp = self._resolve(path)    # 解析路径
        # ... 安全检查、文件读取、截断处理 ...
        return content              # 返回字符串
```

每个 Tool 子类实现自己的 execute，返回字符串或内容块列表。

## 阶段四：结果回传给 LLM

执行结果被包装成 tool 角色消息，追加到对话历史：

```python
# runner.py 第 450 行
tool_message = {
    "role": "tool",
    "tool_call_id": tool_call.id,
    "name": tool_call.name,
    "content": result,              # 工具执行结果
}
messages.append(tool_message)
```

然后 continue 回到循环顶部，再次请求 LLM。LLM 看到工具结果后，决定是继续调用工具还是给出最终回复。

## 一句话总结

LLM 通过 JSON Schema 知道有哪些工具可用 → 返回 tool_call 指明要调用哪个工具、传什么参数 → Runner 通过 prepare_call 查找校验后调用 tool.execute() 实际执行 → 结果回传给 LLM → 循环直到 LLM 给出最终文本回复。

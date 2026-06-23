一、配置阶段：MCP 服务器在哪里定义
配置文件 ~/.nanobot/config.json 中：
{
  "tools": {
    "mcpServers": {
      "my-server": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@some/mcp-server"],
        "env": {},
        "toolTimeout": 30,
        "enabledTools": ["*"]
      }
    }
  }
}
对应 schema.py 中的模型：
Config → ToolsConfig.mcp_servers → dict[str, MCPServerConfig]
MCPServerConfig 支持三种传输方式：
stdio — 启动本地子进程，通过 stdin/stdout 通信
sse — HTTP Server-Sent Events
streamableHttp — HTTP Streamable（自动检测：URL 以 /sse 结尾用 SSE，否则用 streamableHttp）
二、连接阶段：AgentLoop 启动时注册 MCP 工具
2.1 入口
# loop.py: AgentLoop.run()
async def run(self):
    self._running = True
    await self._connect_mcp()          # ← 启动时连接
    ...
2.2 调用链
_connect_mcp()
  → agent_context.connect_mcp(state, registry)
    → mcp_tools.connect_missing_servers(state, registry)
      → connect_mcp_servers(missing, registry)
2.3 核心逻辑 connect_mcp_servers
对每个配置的 MCP 服务器：
Step 1 — 建立连接
stdio:  启动子进程 → stdio_client(params) → (read, write)
sse:    TCP 探测 → sse_client(url) → (read, write)
http:   TCP 探测 → streamable_http_client(url) → (read, write)
Step 2 — 创建会话
session = ClientSession(read, write)
await session.initialize()          # MCP 握手
Step 3 — 发现能力并注册工具
# 注册 Tools
tools = await session.list_tools()
for tool_def in tools.tools:
    wrapper = MCPToolWrapper(session, name, tool_def)
    registry.register(wrapper)

# 注册 Resources（作为只读工具）
resources = await session.list_resources()
for resource in resources.resources:
    wrapper = MCPResourceWrapper(session, name, resource)
    registry.register(wrapper)

# 注册 Prompts（作为工具）
prompts = await session.list_prompts()
for prompt in prompts.prompts:
    wrapper = MCPPromptWrapper(session, name, prompt)
    registry.register(wrapper)
每个 wrapper 的命名规则：mcp_{server_name}_{tool_name}（经过 sanitize）
2.4 工具名过滤
通过 enabledTools 控制：
["*"] — 注册所有工具
["tool_a", "tool_b"] — 只注册指定工具
三、调用阶段：LLM 调用 MCP 工具
MCP 工具注册到 ToolRegistry 后，对 LLM 来说和普通工具完全一样。调用链路：
LLM 返回 tool_call: mcp_my-server_search(query="hello")
  → AgentRunner._run_tool()
    → spec.tools.prepare_call("mcp_my-server_search", {...})
      → 找到 MCPToolWrapper 实例
      → 校验参数
    → wrapper.execute(query="hello")
      → self._session.call_tool("search", arguments={"query": "hello"})
        ← MCP Server 返回结果
      → 返回文本
    → 结果追加到 messages
  → 继续请求 LLM
四、健壮性机制
4.1 瞬态错误重试
# MCPToolWrapper.execute()
except Exception as exc:
    if _is_transient(exc):          # BrokenPipe, ConnectionReset 等
        if not retried_transient:
            retried_transient = True
            await asyncio.sleep(1)  # 退避 1 秒
            continue                # 重试一次
4.2 Session 终止自动重连
# _MCPWrapperBase._refresh_session_after_termination()
if _is_session_terminated(exc):     # "session terminated" / "connection closed"
    refreshed_tool = await self._reconnect(...)   # 重连整个 MCP 服务器
    self._session = refreshed_tool._session       # 替换 session
    continue                                        # 重试
4.3 热重载（WebUI 修改配置后）
# WebUI 发送 RUNTIME_CONTROL_MCP_RELOAD 消息
→ handle_runtime_control()
  → reload_servers()
    → 对比新旧配置，增删改连接
    → 差量更新 registry
五、完整流程图
config.json
  └─ tools.mcpServers: { "my-server": { type, command, ... } }
                          │
                          ▼
AgentLoop.__init__()
  └─ self._mcp_servers = config.tools.mcp_servers
                          │
                          ▼
AgentLoop.run() → _connect_mcp() → connect_missing_servers()
  │
  ├─ 对每个 MCP Server:
  │    ├─ 建立连接 (stdio / sse / http)
  │    ├─ ClientSession.initialize() 握手
  │    ├─ list_tools()    → MCPToolWrapper    → registry.register()
  │    ├─ list_resources() → MCPResourceWrapper → registry.register()
  │    └─ list_prompts()   → MCPPromptWrapper   → registry.register()
  │
  └─ 注册完成，工具进入共享 ToolRegistry
                          │
                          ▼
LLM 请求时:
  spec.tools.get_definitions()  ← 包含 MCP 工具的 JSON Schema
                          │
                          ▼
LLM 返回 tool_call("mcp_my-server_xxx", params)
                          │
                          ▼
MCPToolWrapper.execute(**params)
  → session.call_tool("xxx", arguments=params)  ← 通过 MCP 协议调用远程
  → 返回结果给 LLM
一句话总结：MCP 服务器在 AgentLoop 启动时通过配置建立连接，发现其 tools/resources/prompts 后包装成 MCPToolWrapper 注册到共享的 ToolRegistry。之后 LLM 调用 MCP 工具和调用普通工具走的是同一条路径，只是 execute 内部通过 MCP session 转发给远程服务器执行。
# my-bot 开发路线图

基于 HKUDS/nanobot 架构，分阶段实现核心功能。

当前状态：核心链路已通（CLI → AgentLoop → AgentRunner → Provider → 响应），但 agent 尚无法执行实际任务。

---

## 第一阶段：让 agent 能对话、有记忆

### 1. ContextBuilder 接入 session history

- 现状：`context.py` 只传当前一条消息，LLM 无上下文
- 目标：从 SessionManager 拉取历史消息，拼接进 messages
- 涉及文件：`mybot/agent/context.py`、`mybot/agent/loop.py`

### 2. MemoryStore 基础实现

- 现状：`memory.py` 是空类
- 目标：文件持久化，重启后记忆不丢失
- 涉及文件：`mybot/agent/memory.py`

---

## 第二阶段：让 agent 能做事

### 3. 注册第一个 Tool

- 现状：ToolRegistry 为空，AgentRunner 的 tool 执行链路未跑通
- 目标：实现一个具体 Tool（如 `calculate` 或 `read_file`），验证 ToolRegistry → Tool → execute 全链路
- 涉及文件：新建 `mybot/agent/tools/builtin/`、`mybot/agent/tools/registry.py`

### 4. MCP 连接

- 现状：`mcp.py` 有骨架但未跑通，`_connect_mcp` 已注释
- 目标：接通 MCP server，复用现成工具生态
- 涉及文件：`mybot/agent/tools/mcp.py`、`mybot/agent/loop.py`

---

## 第三阶段：接入外部平台

### 5. 实现第一个 Channel

- 现状：无 channel 实现，bus_loop 模式未验证
- 目标：实现一个 channel（建议 WebSocket 或 Telegram），走通 MessageBus 消费链路
- 涉及文件：新建 `mybot/channels/`

### 6. Gateway 服务

- 现状：main.py 只有 CLI 模式
- 目标：升级为服务端，同时支持多 channel 接入
- 涉及文件：`main.py`、新建 `mybot/gateway/`

---

## 第四阶段：体验完善

### 7. WebUI

- 目标：Vite + React SPA，通过 WebSocket 与 gateway 通信
- 涉及文件：新建 `webui/`

### 8. Dream 记忆整合

- 目标：两阶段记忆压缩（短期 → 长期），减少 token 消耗
- 涉及文件：`mybot/agent/memory.py`

### 9. 更多 Tools / Channels

- 目标：文件读写、shell 执行、web 搜索、更多平台接入
- 涉及文件：`mybot/agent/tools/`、`mybot/channels/`

---

## 建议优先级

先做 **1 → 3**（ContextBuilder + 第一个 Tool），完成后 agent 就能「带上下文对话 + 调用工具」，从 demo 变成可用。

# SubAgent 架构与追踪机制

> SubAgent 是 nanobot 把"重活"从主对话里剥离出来的执行单元：主 agent 调用 spawn 工具提交任务，SubagentManager 在后台启动独立的 LLM + 受限工具集执行，完成后通过消息总线把结果回注到当前会话。

---

## 1. 入口与工具层

- `SpawnTool`（`nanobot/agent/tools/spawn.py`）暴露 `spawn(task, label, temperature)` 接口
- 通过 `ContextAware` 记录调用来源（`channel` / `chat_id` / `session_key` / `message_id`）
- 委托给 `SubagentManager`，同时校验并发上限，避免本地模型同时加载过多 KV 缓存

## 2. 任务调度与并发控制

- `SubagentManager`（`nanobot/agent/subagent.py`）维护以下状态：
  - `_running_tasks` — 运行中的任务映射
  - `_task_statuses` — 任务状态记录
  - `_session_tasks` — 会话级任务关联
- `max_concurrent_subagents` 默认读取 `AgentDefaults`（`nanobot/config/schema.py`，默认 1）
- 每个任务对应一个 `asyncio.Task`，完成回调自动清理映射
- `/stop` 命令可按 session 批量取消

## 3. 受控执行环境

- 每个 subagent 拥有独立的 `ToolRegistry`（`_build_tools`）
- 通过 `ToolLoader(..., scope="subagent")` 只加载 `_scopes` 包含 `subagent` 的工具
  - 例如：ReadFile、EditFile、WriteFile、Shell、Web、Search 等
- 可在 `WorkspaceScope` 下启用沙箱限制
- 系统提示词由 `agent/subagent_system.md` 渲染，包含：
  - 运行上下文
  - workspace 路径
  - 技能摘要

## 4. 运行循环与状态反馈

- `_run_subagent` 以 `AgentRunner.run(AgentRunSpec(...))` 执行受控循环
- `_SubagentHook` 实时更新以下信息：
  - 阶段（phase）
  - 迭代计数（iteration）
  - 工具事件（tool events）
  - 用量统计（usage）
- 若遭遇 `tool_error` 或其他异常，会格式化最后几步进度（`_format_partial_progress`）

## 5. 结果回传与会话注入

- 任务结束时 `_announce_result` 按 `agent/subagent_announce.md` 模板生成摘要
- 通过 `MessageBus.publish_inbound` 注入为特殊消息：
  - `channel = "system"`
  - `injected_event = "subagent_result"`
- 主 agent 的 `AgentLoop._persist_subagent_followup` 在 turn 前去重并写入 session，防止同一 task 重复记录

## 6. 前端与外部展示

- 为避免把 `Task:` 指令和 Summarize 提示暴露给用户
- `scrub_subagent_announce_body`（`nanobot/utils/subagent_channel_display.py`）在以下场景过滤内容：
  - WebSocket 推送
  - Session preview
  - UI 组件
- 外部渠道只保留 header + 截断后的 Result，展示简明进度

## 7. 可观测与回滚

- `SubagentStatus` 记录以下字段：

| 字段 | 说明 |
| --- | --- |
| `task_id` | 任务唯一标识 |
| `label` | 任务标签 |
| `task_description` | 任务描述 |
| `started_at` | 开始时间 |
| `phase` | 当前阶段 |
| `iteration` | 迭代次数 |

- `/status` 和 `cmd_stop` 可查询并终止当前 session 的 subagent
- 主会话可通过 `cancel_by_session` 或 `get_running_count` 获取/控制状态

---

## 设计理念

> **受控隔离 + 结果可追溯**

SubAgent 享有必要的工具能力但被限制在显式 scope 内，任何结果都经过模板化摘要与注入流程，再由主 agent 以自然语言回报用户，从而在保持响应速度的同时避免系统提示或任务细节外泄。

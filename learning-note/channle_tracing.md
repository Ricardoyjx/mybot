# Channel 架构设计

## 设计思路

你已经有 `MessageBus` 和 `InboundMessage/OutboundMessage`，Channel 的核心职责就是：

外部平台消息 → InboundMessage，OutboundMessage → 外部平台回复

```text
Telegram ──┐
Discord  ──┤                    ┌─────────────┐
WeChat   ──┼── ChannelManager ──┤ MessageBus  ├── AgentLoop
Slack    ──┤                    └─────────────┘
WebChat  ──┘  (已有)
CLI      ──┘  (已有)
```

## 建议的文件结构

```text
mybot/channels/
├── __init__.py
├── base.py          # Channel 抽象基类
├── manager.py       # ChannelManager — 发现、启动、协调
├── telegram.py      # Telegram Bot
├── discord.py       # Discord Bot (以后)
└── webhook.py       # 通用 Webhook (最简单，优先做)
```

## 核心接口设计

### 1. base.py — Channel 基类

```python
class Channel(ABC):
    name: str                    # "telegram", "webhook", etc.

    @abstractmethod
    async def start(self, bus: MessageBus) -> None:
        """启动监听，收到消息时往 bus.publish_inbound(msg)"""

    @abstractmethod
    async def stop(self) -> None:
        """优雅关闭"""

    async def send(self, msg: OutboundMessage) -> None:
        """把 agent 回复发回平台（可选覆写）"""
```

关键点：Channel **只负责收发**，不处理业务逻辑。所有消息通过 `MessageBus` 与 `AgentLoop` 解耦。

### 2. manager.py — ChannelManager

```python
class ChannelManager:
    def __init__(self, bus: MessageBus, config: dict):
        self._channels: dict[str, Channel] = {}

    async def start_all(self) -> None:
        """启动所有配置的 channel"""

    async def stop_all(self) -> None:
        """关闭所有 channel"""
```

### 3. 集成到 AgentLoop

你现有的 `_dispatch` 已经处理 `InboundMessage`，唯一需要加的是：当 `OutboundMessage` 发布到 bus 时，根据 `msg.channel` 路由到对应的 Channel 发送。

## 建议的实现顺序

### 第一步：Webhook Channel（最简单，1-2 小时）

一个通用 HTTP 端点，接收 JSON 消息，返回 JSON 响应。适合对接飞书、钉钉、企业微信等任何支持 Webhook 的平台。

```text
POST /api/webhook → InboundMessage → AgentLoop → OutboundMessage → JSON 响应
```

这一步能验证 Channel 架构是否正确。

### 第二步：Telegram Channel（最实用）

用 `python-telegram-bot` 或直接调 Telegram Bot API，支持：

- 长轮询或 Webhook 模式
- 文本消息收发
- 流式输出（通过编辑消息实现）

### 第三步：ChannelManager + 配置化

把 channel 配置加到 `config.json` 里：

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "xxx",
      "allowed_users": ["ricardo"]
    },
    "webhook": {
      "enabled": true,
      "port": 9000
    }
  }
}
```

## 需要注意的点

- **session_key 要跟 channel 绑定** — Telegram 用 `tg:{chat_id}`，Webhook 用 `wh:{sender_id}`，这样同一个用户在不同平台有独立会话。

- **OutboundMessage 路由** — 你现有的 bus 是单向的（只有 inbound），需要加 outbound 消费。建议在 `AgentLoop._dispatch` 里，处理完消息后直接调用对应 channel 的 `send()`。

- **流式输出** — Web 端用 WebSocket 很自然，但 Telegram/飞书不支持原生流式。策略是：先发一条消息，然后不断 edit 它（Telegram）或等完整回复后一次性发（飞书）。

- **权限控制** — 不同平台需要不同的鉴权方式（Telegram 检查 user_id，Webhook 检查签名）。

"""Telegram Bot Channel — 通过 python-telegram-bot 接入 Telegram。

配置方式：
    环境变量 TELEGRAM_BOT_TOKEN 或 config 传入。
"""

from loguru import logger
from mybot.channels.base import Channel
from mybot.bus.events import InboundMessage, OutboundMessage


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(
        self, token: str | None = None, allowed_users: list[str] | None = None
    ):
        import os

        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.allowed_users = allowed_users  # None = 允许所有人
        self._app = None
        self._running = False

    async def start(self, on_message) -> None:
        from telegram.ext import Application, MessageHandler, filters

        if not self.token:
            raise ValueError(
                "TelegramChannel: 缺少 bot token，设置 TELEGRAM_BOT_TOKEN 环境变量"
            )

        self._app = Application.builder().token(self.token).build()
        self._on_message = on_message

        # 注册消息处理器
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

        self._running = True
        logger.info("TelegramChannel: started, polling...")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        self._running = False
        logger.info("TelegramChannel: stopped")

    async def _handle_message(self, update, context) -> None:
        """收到 Telegram 消息时的回调。"""
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat

        if not msg or not msg.text or not user:
            return

        # 权限检查
        user_id = str(user.id)
        if self.allowed_users and user_id not in self.allowed_users:
            logger.debug("TelegramChannel: ignored user {}", user_id)
            return

        # 群聊需要 @机器人 才回复
        is_group = chat.type in ("group", "supergroup")
        if is_group and not msg.entities:
            return
        if is_group:
            bot_username = context.bot.username
            if not any(
                e.type == "mention"
                and msg.text[e.offset : e.offset + e.length] == f"@{bot_username}"
                for e in (msg.entities or [])
            ):
                return

        content = msg.text
        # 去掉 @bot 前缀
        if is_group:
            import re

            content = re.sub(r"@\w+\s*", "", content).strip()

        inbound = InboundMessage(
            sender_id=user_id,
            chat_id=str(chat.id),
            content=content,
            channel="telegram",
            metadata={
                "is_group": is_group,
                "msg_id": msg.message_id,
                "username": user.username or user.first_name,
                "_wants_stream": False,
            },
        )
        logger.info("TelegramChannel: [{}] {}", user.username or user_id, content[:50])

        if self._on_message:
            await self._on_message(inbound)

    async def send(self, msg: OutboundMessage) -> None:
        if not self._app:
            return
        try:
            await self._app.bot.send_message(
                chat_id=int(msg.chat_id),
                text=msg.content,
                parse_mode="Markdown",
            )
            logger.debug("TelegramChannel: sent to {}", msg.chat_id)
        except Exception as e:
            logger.error("TelegramChannel: send failed: {}", e)

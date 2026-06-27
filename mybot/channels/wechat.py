import asyncio
from loguru import logger
from mybot.channels.base import Channel
from mybot.bus.events import InboundMessage, OutboundMessage


class WeChatChannel(Channel):
    name = "wechat"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._wcf = None
        self._running = False
        self._on_message = None
        self._receive_task = None

    async def start(self, on_message) -> None:
        from wcferry import Wcf

        self._on_message = on_message
        self._wcf = Wcf()
        self._wcf.enable_receiving_msg()
        self._running = True

        logger.info("WeChatChannel: started, wxid={}", self._wcf.get_self_wxid())

        self._receive_task = asyncio.create_task(self._receive_loop())

    async def stop(self) -> None:
        self._running = False
        if self._wcf:
            self._wcf.disable_recv_msg()
        if self._receive_task:
            self._receive_task.cancel()
        logger.info("WeChatChannel: stopped")

    async def _receive_loop(self) -> None:
        """在后台线程中接收消息，转换为 InboundMessage 发布到 bus。"""
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # get_msg() 是阻塞的，放到线程池执行
                msg = await loop.run_in_executor(None, self._wcf.get_msg)

                # 只处理文本消息（type=1）
                if msg.type != 1:
                    logger.debug("WeChatChannel: ignoring msg type={}", msg.type)
                    continue

                # 群聊消息需要 @机器人 才回复
                is_group = bool(msg.roomid)
                if is_group:
                    if not self._is_at_me(msg):
                        continue
                    content = self._strip_at(msg.content)
                    sender = msg.sender
                    chat_id = msg.roomid
                else:
                    content = msg.content
                    sender = msg.sender
                    chat_id = msg.sender

                inbound = InboundMessage(
                    sender_id=sender,
                    chat_id=chat_id,
                    content=content,
                    channel="wechat",
                    metadata={"is_group": is_group, "msg_id": msg.id, "_wants_stream": False},
                )

                if self._on_message:
                    await self._on_message(inbound)

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error("WeChatChannel: receive error: {}", e)
                await asyncio.sleep(1)

    async def send(self, msg: OutboundMessage) -> None:
        if not self._wcf:
            return
        try:
            self._wcf.send_text(msg.content, msg.chat_id)
            logger.debug("WeChatChannel: sent to {}", msg.chat_id)
        except Exception as e:
            logger.error("WeChatChannel: send failed: {}", e)

    def _is_at_me(self, msg) -> bool:
        """检查群消息是否 @了机器人。"""
        self_wxid = self._wcf.get_self_wxid()
        return f"@{self_wxid}" in msg.content

    def _strip_at(self, content: str) -> str:
        """去掉 @机器人 的前缀。"""
        import re

        return re.sub(r"@[\w]+\s*", "", content).strip()

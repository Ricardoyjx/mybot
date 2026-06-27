"""MockChannel — 用于测试 Channel 架构的模拟频道。

从 stdin 读取消息，发送到 MessageBus，
agent 回复后打印到 stdout。
"""

import asyncio
import sys
from loguru import logger
from mybot.channels.base import Channel
from mybot.bus.events import InboundMessage, OutboundMessage


class MockChannel(Channel):
    name = "mock"

    def __init__(self):
        self._running = False
        self._on_message = None

    async def start(self, on_message) -> None:
        self._on_message = on_message
        self._running = True
        logger.info("MockChannel: started, reading from stdin")

        # 后台任务读取 stdin
        asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        self._running = False
        logger.info("MockChannel: stopped")

    async def _read_loop(self) -> None:
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break
                content = line.decode("utf-8").strip()
                if not content:
                    continue

                inbound = InboundMessage(
                    sender_id="mock_user",
                    chat_id="mock_chat",
                    content=content,
                    channel="mock",
                    metadata={"_wants_stream": False},
                )
                logger.info("MockChannel: received '{}'", content)

                if self._on_message:
                    await self._on_message(inbound)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("MockChannel: read error: {}", e)
                await asyncio.sleep(0.5)

    async def send(self, msg: OutboundMessage) -> None:
        print(f"\n🤖 [{msg.channel}:{msg.chat_id}] {msg.content}\n", flush=True)
        logger.debug("MockChannel: sent response")

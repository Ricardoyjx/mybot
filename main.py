"""
nanobot CLI 入口

启动交互式命令行，通过 AgentLoop 处理用户消息。

配置方式（环境变量）：
    OPENAI_API_KEY   - OpenAI API 密钥（必须）
    OPENAI_BASE_URL  - 自定义接口地址（可选，兼容 Azure/本地代理等）
    OPENAI_MODEL     - 模型名称（可选，默认 gpt-4o-mini）
"""

import asyncio
import os
import sys

from loguru import logger

from mybot.agent.loop import AgentLoop
from mybot.bus.queue import MessageBus
from mybot.session.manager import SessionManager


SESSION_KEY = "cli:default"


def create_provider():
    """根据环境变量自动选择 provider。"""
    if os.getenv("OPENAI_API_KEY"):
        from mybot.providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    else:
        logger.warning("未设置 OPENAI_API_KEY，使用 stub provider（无法真正对话）")
        from mybot.providers.stub import StubProvider
        return StubProvider()


def create_agent() -> AgentLoop:
    """创建并返回 AgentLoop 实例。"""
    return AgentLoop(
        bus=MessageBus(),
        provider=create_provider(),
        model=None,
        session_manager=SessionManager(),
    )


async def cli_loop(agent: AgentLoop) -> None:
    """交互式 CLI 主循环。"""
    print("nanobot CLI (输入 /quit 退出)", flush=True)
    print("-" * 40, flush=True)

    loop = asyncio.get_running_loop()

    while True:
        try:
            user_input = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            print("\n再见!", flush=True)
            break

        if not user_input:
            break

        text = user_input.strip()
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            print("再见!", flush=True)
            break

        try:
            response = await agent.process_direct(
                content=text,
                session_key=SESSION_KEY,
            )
            if response and response.content:
                print(f"bot> {response.content}", flush=True)
            else:
                print("bot> (无响应)", flush=True)
        except Exception as e:
            logger.debug("process_direct 异常: {}", e)
            print(f"bot> [错误] {e}", flush=True)


async def bus_loop(agent: AgentLoop) -> None:
    """消息总线模式：消费 inbound 队列，打印 outbound 响应。

    适合对接 channel 时使用：
        await agent.bus.publish_inbound(msg)
    """
    logger.info("总线模式启动，等待消息...")

    async def _print_responses():
        while True:
            out = await agent.bus.consume_outbound()
            print(f"[{out.channel}:{out.chat_id}] {out.content}", flush=True)

    await asyncio.gather(
        _print_responses(),
        agent.run(),
    )


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

    agent = create_agent()

    try:
        asyncio.run(cli_loop(agent))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

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
from pathlib import Path

from loguru import logger

from mybot.agent.loop import AgentLoop
from mybot.bus.queue import MessageBus
from mybot.session.manager import SessionManager
from mybot.config.schema import MCPServerConfig

SESSION_KEY = "cli:default"


def create_provider():
    """根据环境变量自动选择 provider。"""
    if os.getenv("DEEPSEEK_API_KEY"):
        from mybot.providers.deepseek_provider import DeepseekProvider

        return DeepseekProvider(
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        )
    elif os.getenv("MIMO_API_KEY"):
        from mybot.providers.mimo_provider import MimoProvider

        return MimoProvider(
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
            model="mimo-v2.5-pro",
        )
    else:
        logger.warning(
            "未设置 API Key (DEEPSEEK_API_KEY / MIMO_API_KEY)，使用 stub provider（无法真正对话）"
        )
        from mybot.providers.stub import StubProvider

        return StubProvider()


def create_agent() -> AgentLoop:
    """创建并返回 AgentLoop 实例。"""
    return AgentLoop(
        bus=MessageBus(),
        provider=create_provider(),
        model=None,
        session_manager=SessionManager(workspace=Path.home() / "Projects" / "my-bot"),
        mcp_servers={
            "filesystem": MCPServerConfig(
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", "/home/ricardo"],
                enabled_tools=[
                    "list_directory",
                    "directory_tree",
                    "list_allowed_directories",
                ],
            )
        },
    )


async def cli_loop(agent: AgentLoop) -> None:
    """交互式 CLI 主循环。"""
    print("nanobot CLI (输入 /quit 退出)", flush=True)
    print("-" * 40, flush=True)

    loop = asyncio.get_running_loop()

    try:
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

            # 流式输出状态
            streaming_started = False

            async def on_stream(delta: str) -> None:
                nonlocal streaming_started
                if not streaming_started:
                    sys.stdout.write("bot> ")
                    streaming_started = True
                sys.stdout.write(delta)
                sys.stdout.flush()

            async def on_stream_end() -> None:
                pass

            try:
                response = await agent.process_direct(
                    content=text,
                    session_key=SESSION_KEY,
                    on_stream=on_stream,
                    on_stream_end=on_stream_end,
                )
                # 流式已输出过，补个换行
                if streaming_started:
                    print(flush=True)
                # 如果没有流式输出但有响应，打印完整内容
                elif response and response.content:
                    print(f"bot> {response.content}", flush=True)
                else:
                    print("bot> (无响应)", flush=True)
            except Exception as e:
                logger.debug("process_direct 异常: {}", e)
                print(f"bot> [错误] {e}", flush=True)
    finally:
        await agent.shutdown()


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


async def web_loop(agent: AgentLoop, host: str = "0.0.0.0", port: int = 8080) -> None:
    """Web 模式：启动 HTTP + WebSocket 服务。"""
    from mybot.api.server import WebServer

    server = WebServer(agent, host=host, port=port)
    agent._register_default_tools()
    await agent._connect_mcp()
    await server.start()

    try:
        await asyncio.Event().wait()  # 永久运行
    finally:
        await agent.shutdown()


async def wechat_loop(agent: AgentLoop) -> None:
    """WeChat 模式：通过 WeChatFerry 接收微信消息。"""
    from mybot.channels.wechat import WeChatChannel

    channel = WeChatChannel()
    agent._register_default_tools()
    await agent._connect_mcp()

    # 消息回调 发布到bus
    async def on_message(msg):
        await agent.bus.publish_inbound(msg)

    # outbound
    async def outbound_loop():
        while True:
            out = await agent.bus.consume_outbound()
            if out.channel == "wechat":
                await channel.send(out)

    await channel.start(on_message)
    asyncio.create_task(outbound_loop())

    try:
        await asyncio.Event().wait()
    finally:
        await channel.stop()


async def mock_loop(agent: AgentLoop) -> None:
    """Mock 模式：从 stdin 读消息，验证 Channel 架构。"""
    from mybot.channels.mock import MockChannel

    channel = MockChannel()
    agent._register_default_tools()
    await agent._connect_mcp()

    async def on_message(msg):
        await agent.bus.publish_inbound(msg)

    async def outbound_loop():
        while True:
            out = await agent.bus.consume_outbound()
            if out.channel == "mock":
                await channel.send(out)

    await channel.start(on_message)
    asyncio.create_task(outbound_loop())

    try:
        await asyncio.Event().wait()
    finally:
        await channel.stop()


async def telegram_loop(agent: AgentLoop) -> None:
    """Telegram 模式：通过 Telegram Bot 接收消息。"""
    from mybot.channels.telegram import TelegramChannel

    channel = TelegramChannel()
    agent._register_default_tools()
    await agent._connect_mcp()

    async def on_message(msg):
        await agent.bus.publish_inbound(msg)

    async def outbound_loop():
        while True:
            out = await agent.bus.consume_outbound()
            if out.channel == "telegram":
                await channel.send(out)

    await channel.start(on_message)
    asyncio.create_task(outbound_loop())
    agent_task = asyncio.create_task(agent.run())

    try:
        await asyncio.Event().wait()
    finally:
        agent_task.cancel()
        await channel.stop()
        await agent.shutdown()


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

    args = sys.argv[1:]
    agent = create_agent()

    try:
        if "--web" in args:
            port = 8080
            for i, arg in enumerate(args):
                if arg == "--port" and i + 1 < len(args):
                    port = int(args[i + 1])
            asyncio.run(web_loop(agent, port=port))
        elif "--wechat" in args:
            asyncio.run(wechat_loop(agent))
        elif "--telegram" in args:
            asyncio.run(telegram_loop(agent))
        elif "--mock" in args:
            asyncio.run(mock_loop(agent))
        else:
            asyncio.run(cli_loop(agent))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

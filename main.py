from mybot.agent.loop import AgentLoop
from mybot.bus.queue import MessageBus


def _run_gateway():
    agent = AgentLoop(
        bus=MessageBus(),
        providers="",
        model="",
    )
    pass

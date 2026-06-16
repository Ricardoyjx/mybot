from bot.loop import AgentLoop
from bus.queue import MessageBus

def _run_gateway():
    agent = AgentLoop(
        bus= MessageBus(),
        providers= "",
        model = "",
    )   
    pass
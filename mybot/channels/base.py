from abc import ABC, abstractmethod
from mybot.bus.events import InboundMessage, OutboundMessage


class Channel(ABC):
    name: str = "unknown"

    @abstractmethod
    async def start(self, on_message) -> None:
        """"""

    @abstractmethod
    async def stop(self) -> None:
        """"""

    async def send(self, msg: OutboundMessage) -> None:
        """"""

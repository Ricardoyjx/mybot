from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:

    channel: str
    sender_id: str
    chat_id: str
    content: str
    timestamp: datetime
    metadata: dict[str, any]
    session_key_override: str | None

    @property
    def session_key(self) -> str:
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str]
    metadata: dict[str, Any]
    buttons: list[list[str]]

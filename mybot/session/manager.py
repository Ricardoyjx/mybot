from dataclasses import dataclass, field
from typing import Any
from datetime import datetime


@dataclass
class Session:
    session_key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_user_message(self, text: str, **extra: Any) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": text,
                "timestamp": self.updated_at.isoformat(),
                **extra,
            }
        )
        self.updated_at = datetime.now()

    def add_assistant_message(self, text: str, **extra: Any) -> None:
        self.messages.append(
            {
                "role": "assistant",
                "content": text,
                "timestamp": self.updated_at.isoformat(),
                **extra,
            }
        )
        self.updated_at = datetime.now()

    def get_history(self, limit: int = 0) -> list[dict[str, Any]]:
        if limit <= 0:
            return list(self.messages)
        return list(self.messages[-limit:])


@dataclass
class SessionManager:
    sessions: dict[str, Session] = field(default_factory=dict)

    def get_or_create(self, session_key: str) -> Session:
        if session_key not in self.sessions:
            self.sessions[session_key] = Session(session_key=session_key)
        return self.sessions[session_key]

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from datetime import datetime
import re
from mybot.utils.helpers import ensure_dir

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


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
    def __init__(self, workspace: Path):
        self.workspace = self.workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self._cache: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    @staticmethod
    def safe_key(key: str) -> str:
        return _UNSAFE_CHARS.sub("_", key.replace(":", "_")).strip()

    def _get_session_path(self, key: str) -> Path:
        """get the file path for a session"""
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def _load(self, session_key) -> Session | None:
        pass

    def save(self) -> None:
        pass

    def invalidate(self, key: str) -> None:
        pass

    def delete_session(self, key: str) -> bool:
        pass

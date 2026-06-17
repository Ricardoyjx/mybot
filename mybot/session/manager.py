from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any
from datetime import datetime
import re, json
from mybot.utils.helpers import ensure_dir
from loguru import logger

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass
class Session:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_user_message(self, text: str, **extra: Any) -> None:
        self.updated_at = datetime.now()
        self.messages.append(
            {
                "role": "user",
                "content": text,
                "timestamp": self.updated_at.isoformat(),
                **extra,
            }
        )

    def add_assistant_message(self, text: str, **extra: Any) -> None:
        self.updated_at = datetime.now()
        self.messages.append(
            {
                "role": "assistant",
                "content": text,
                "timestamp": self.updated_at.isoformat(),
                **extra,
            }
        )

    def get_history(self, limit: int = 0) -> list[dict[str, Any]]:
        if limit <= 0:
            return list(self.messages)
        return list(self.messages[-limit:])


@dataclass
class SessionManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace
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

    def _load(self, key: str) -> Session | None:
        """load a session from disk"""
        path = self._get_session_path(key)
        if not path.exists():
            logger.error("session file not found {}", key)
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = (
                            datetime.fromisoformat(data["created_at"])
                            if data.get("created_at")
                            else None
                        )
                        updated_at = (
                            datetime.fromisoformat(data["updated_at"])
                            if data.get("updated_at")
                            else None
                        )
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated,
            )

        except Exception as e:
            logger.warning("Session file not found {}:{}", key, e)
            return None

    def save(self, session: Session, *, fsync: bool = True) -> None:
        """save a session to disk atomically"""
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(".jsonl.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated,
                }
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp_path, path)

        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        path = self._get_session_path(key)
        self.invalidate(key)
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.warning("Failed to delete session file{}:{}", path, e)
            return False

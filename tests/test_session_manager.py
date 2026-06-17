import json
from pathlib import Path

from mybot.session.manager import SessionManager


def test_save_creates_jsonl_and_roundtrips(tmp_path: Path):
    manager = SessionManager(workspace=tmp_path)
    session = manager.get_or_create("unit:test")

    session.add_user_message("hello")
    session.add_assistant_message("world")

    manager.save(session, fsync=True)

    session_file = manager._get_session_path("unit:test")
    assert session_file.exists(), "session file should be created"

    lines = [line for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 3

    first = json.loads(lines[0])
    assert first.get("_type") == "metadata"

    messages = [json.loads(line) for line in lines[1:]]
    roles = [m.get("role") for m in messages]
    assert roles == ["user", "assistant"]

    reloaded = manager.get_or_create("unit:test")
    assert len(reloaded.messages) == 2
    assert reloaded.messages[-1].get("role") == "assistant"

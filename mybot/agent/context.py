from mybot.agent.memory import MemoryStore
from mybot.agent.tools import mcp as mcp_tools
from mybot.agent.tools.registry import ToolRegistry
from mybot.agent.skills import SkillsLoader
from typing import Any, Mapping, Sequence
from pathlib import Path
from utils.helpers import truncate_text


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    return await mcp_tools.connect_missing_servers(state, tools)


DEFAULT_SYSTEM_PROMPT = (
    "你是一个有用的 AI 助手。你可以通过调用工具来完成任务，"
    "例如读取文件、搜索信息等。当用户要求你读取或分析文件时，调用工具。"
)


class ContextBuilder:

    def __init__(
        self,
        workspace: Path,
        timezone: str | None = None,
        disabled_skills: list[str] | None = None,
    ):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(
            workspace, disabled_skills=set(disabled_skills) if disabled_skills else None
        )

    def _build_user_content(
        self, text: str, media: list[str] | None
    ) -> str | list[dict[str, Any]]:
        if not media:
            return text

        # handler media
        images = []
        # for path in media:
        #     p = Path(path)
        #     if not p.is_file():
        #         continue
        #     raw = p.read_bytes()

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def _build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        session_summary: str | None = None,
        workspace: Path | None = None,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        unified_session: bool = False,
    ):
        """Build the system prompt from identity, bootstrap files, memory, and skills."""

        # 获取root根目录,如果参数没有就找类属性
        root = workspace or self.workspace
        # 列表存储parts
        parts = []

        # 获取身份
        id = self._get_identity(channel, workspace=root)
        parts.append(id)

        # 读取bootstrap file
        bootstrap = self._load_bootstrap_files(root)
        if bootstrap:
            parts.append(bootstrap)
        # 读取memory
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")
        # 读取skills

        # always_skills = self.skills.get_always_skills()
        # if always_skills:
        #     always_content = self.skills.load_skills_for_content(always_skills)
        #     if always_content:
        #         parts.append(f"# Active Skills\n\n{always_content}")

        # 读取 skills summary

        # skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        # if skills_summary:
        #     parts.append(
        #         render_template(
        #             "agent/skills_section.md", skills_summary=skills_summary
        #         )
        #     )

        # 读取最近历史memory
        if include_memory_recent_history:
            entries = self.memory.read_recent_history_for_prompt(
                since_cursor=self.memory.get_last_dream_cursor(),
                session_key=session_key,
                unified_session=unified_session,
            )
            if entries:
                capped = entries[-self._MAX_RECENT_HISTORY :]
                history_text = "\n".join(
                    f"- [{e['timestamp']}] {e['content']}" for e in capped
                )
                history_text = truncate_text(history_text, self._MAX_HISTORY_CHARS)
                parts.append("# Recent History\n\n" + history_text)

        # 读取session summary
        if session_summary:
            parts.append("[Archived Context Summary]\n\n{session_summary}")
        # 返回str结果
        return "\n\n---\n\n".join(parts)

    def _merge_message_content():
        pass

    def build_messages(
        self,
        history: list[dict[str, Any]] | None = None,
        current_message: str = "",
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        sender_id: str | None = None,
        session_summary: str | None = None,
        session_metadata: Mapping[str, Any] | None = None,
        current_runtime_lines: Sequence[str] | None = None,
        workspace: Path | None = None,
        runtime_state: Any | None = None,
        inbound_message: Any | None = None,
        skip_runtime_lines: bool = False,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        unified_session: bool = False,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""

        root = workspace or self.workspace

        runtime_ctx = self._build_runtime_context(
            channel,
            chat_id,
            self.timezone,
            sender_id,
            # supplemental_lines=extra or None,
        )

        user_content = self._build_user_content(current_message, media)

        if isinstance(user_content, str):
            merged = f"{user_content}\n\n{runtime_ctx}"
        else:
            merged = user_content + [{"type": "text", "text": runtime_ctx}]

        messages = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    skill_names,
                    channel=channel,
                    session_summary=session_summary,
                    workspace=root,
                    include_memory_recent_history=include_memory_recent_history,
                    session_key=session_key,
                    unified_session=unified_session,
                ),
            },
            *history,  # 解包元组
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
        messages.append({"role": current_role, "content": merged})
        return messages

from mybot.agent.memory import MemoryStore
from mybot.agent.tools import mcp as mcp_tools
from mybot.agent.tools.registry import ToolRegistry

# from mybot.agent.skills import SkillsLoader
from typing import Any, Mapping, Sequence
from pathlib import Path
from mybot.utils.helpers import current_time_str, truncate_text


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    return await mcp_tools.connect_missing_servers(state, tools)


DEFAULT_SYSTEM_PROMPT = (
    "你是一个有用的 AI 助手。你可以通过调用工具来完成任务。"
    "\n\n可用工具及使用场景："
    "\n- web_search: 搜索互联网，获取实时信息（天气、新闻、最新资讯等）。"
    "需要联网信息时必须使用此工具。"
    "\n- web_fetch: 读取指定 URL 的网页内容。"
    "搜索到有用链接后，用此工具读取页面获取详细数据。"
    "\n- read_file: 读取本地文件内容。"
    "\n- MCP 文件系统工具（mcp_filesystem_*）: 仅用于本地文件操作"
    "（列出目录、搜索本地文件等），不要用于查询互联网信息。"
    "\n\n工具使用规则："
    "\n- 搜索最多 2 次。搜到结果后，用 web_fetch 读取最相关的页面获取详细数据。"
    "\n- 必须根据获取的实际数据直接回答用户，不要只给链接。"
    "\n- 如果无法获取精确数据，根据已有信息给出合理回答。"
    "\n\n你拥有跨会话的持久记忆，Recent History 部分记录了之前的对话内容，"
    "你可以从中回忆用户的信息和之前的交流。"
)


class ContextBuilder:

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _MAX_RECENT_HISTORY = 50
    _MAX_HISTORY_CHARS = 32_000  # hard cap on recent history section size
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"

    def __init__(
        self,
        workspace: Path,
        timezone: str | None = None,
        disabled_skills: list[str] | None = None,
    ):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        # self.skills = SkillsLoader(
        #     workspace, disabled_skills=set(disabled_skills) if disabled_skills else None
        # )

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
        identity = DEFAULT_SYSTEM_PROMPT
        parts.append(identity)

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
                    f"- [{e.get('timestamp', '')}] {e.get('content', '')}"
                    for e in capped
                )
                history_text = truncate_text(history_text, self._MAX_HISTORY_CHARS)
                parts.append("# Recent History\n\n" + history_text)

        # 读取session summary
        if session_summary:
            parts.append(f"[Archived Context Summary]\n\n{session_summary}")
        # 返回str结果
        return "\n\n---\n\n".join(parts)


    def _load_bootstrap_files(self, workspace: Path | None = None) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        root = workspace or self.workspace

        for filename in self.BOOTSTRAP_FILES:
            file_path = root / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _build_runtime_context(
        channel: str | None,
        chat_id: str | None,
        timezone: str | None = None,
        sender_id: str | None = None,
        supplemental_lines: Sequence[str] | None = None,
    ) -> str:
        """Build untrusted runtime metadata block appended after user content."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if sender_id:
            lines += [f"Sender ID: {sender_id}"]
        if supplemental_lines:
            lines.extend(supplemental_lines)
        return (
            ContextBuilder._RUNTIME_CONTEXT_TAG
            + "\n"
            + "\n".join(lines)
            + "\n"
            + ContextBuilder._RUNTIME_CONTEXT_END
        )

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
                "content": self._build_system_prompt(
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

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    (
                        item
                        if isinstance(item, dict)
                        else {"type": "text", "text": str(item)}
                    )
                    for item in value
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

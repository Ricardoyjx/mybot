from mybot.agent.memory import MemoryStore
from mybot.agent.tools import mcp as mcp_tools
from mybot.agent.tools.registry import ToolRegistry

from mybot.agent.skills import SkillsLoader
from typing import Any, Mapping, Sequence
from pathlib import Path
from mybot.utils.helpers import current_time_str, truncate_text


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    return await mcp_tools.connect_missing_servers(state, tools)


DEFAULT_SYSTEM_PROMPT = (
    "你是一个有用的 AI 助手。你可以通过调用工具来完成任务。"
    "\n\n## 严格工作流程"
    "\n\n收到用户问题时，严格按以下步骤执行："
    "\n1. 判断问题类型。如果属于以下任何一类，必须调用 web_search："
    "\n   - 体育赛事比分、晋级、赛程（世界杯、联赛、奥运会等）"
    "\n   - 天气、气温、空气质量"
    "\n   - 新闻事件、时事热点"
    "\n   - 股票、汇率、加密货币等金融数据"
    "\n   - 电影票房、综艺收视、排行榜等时效性数据"
    "\n   - 任何含`今天/最近/现在/最新`的问题"
    "\n   不确定时，宁可搜索也不要凭记忆回答。你的训练数据可能已过时。"
    "\n2. 从搜索结果中选取最相关的 1-2 个链接，调用 web_fetch 读取页面内容。"
    "\n3. 根据读取到的内容，直接回答用户。"
    "\n\n## 铁律（违反即失败）"
    "\n\n- web_search 最多调用 2 次。搜完立刻用 web_fetch 读页面，然后回答。"
    "\n- 绝对不要只给用户链接。必须读取页面内容后给出具体答案。"
    "\n- 如果 2 次搜索都没有找到精确数据，立刻停止搜索，根据已有信息给出最佳回答。"
    "\n- 严禁重复搜索相同或相似的关键词。一次搜不到就换思路或直接回答。"
    "\n\n## 可用工具"
    "\n\n- web_search: 搜索互联网，获取实时信息（天气、新闻、最新资讯等）。"
    "\n- web_fetch: 读取指定 URL 的网页内容，配合 web_search 使用。"
    "\n- read_file: 读取本地文件内容。"
    "\n- MCP 文件系统工具（mcp_filesystem_*）: 仅用于本地文件操作，不要用于查询互联网信息。"
    "\n\n## 记忆"
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
        # 读取 always skills
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_content(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        # 读取 triggered skills
        if skill_names:
            triggered = [self.skills.skills[n] for n in skill_names if n in self.skills.skills]
            if triggered:
                triggered_content = self.skills.load_skills_for_content(triggered)
                if triggered_content:
                    parts.append(f"# Triggered Skills\n\n{triggered_content}")

        # skills summary
        exclude_names = {s.name for s in always_skills}
        skills_summary = self.skills.build_skills_summary(exclude=exclude_names)
        if skills_summary:
            parts.append(f"# Available Skills\n\n{skills_summary}")

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

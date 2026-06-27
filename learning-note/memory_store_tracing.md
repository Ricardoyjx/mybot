# Memory Store 设计文档

## 整体架构

Memory 系统由三层组成：

```text
┌─────────────────────────────────────────────┐
│            Context Builder                  │  ← 注入到 LLM 系统提示
│  (context.py)                               │
├─────────────────────────────────────────────┤
│         Consolidator + AutoCompact          │  ← 自动压缩 & Dream
│  (memory.py)        (autocompact.py)        │
├─────────────────────────────────────────────┤
│            MemoryStore                      │  ← 纯文件 I/O 层
│  (memory.py)                                │
├─────────────────────────────────────────────┤
│            GitStore                         │  ← Git 版本控制
│  (utils/gitstore.py)                        │
└─────────────────────────────────────────────┘
```

## 一、MemoryStore — 纯文件 I/O 层

管理四类文件：

| 文件 | 路径 | 用途 |
| --- | --- | --- |
| MEMORY.md | `workspace/memory/MEMORY.md` | 长期记忆（事实、偏好） |
| history.jsonl | `workspace/memory/history.jsonl` | 对话历史（追加写入） |
| SOUL.md | `workspace/SOUL.md` | AI 人格/身份定义 |
| USER.md | `workspace/USER.md` | 用户个人信息 |

### 1.1 基本读写

```python
def read_memory(self) -> str:
    return self.read_file(self.memory_file)

def write_memory(self, content: str) -> None:
    self.memory_file.write_text(content, encoding="utf-8")
```

SOUL.md、USER.md 同理。这些是简单的 Markdown 文件，LLM 可以通过 my 工具修改。

### 1.2 history.jsonl — 追加式历史

写入 — `append_history()`：

```python
def append_history(self, entry, *, max_chars=None, session_key=None) -> int:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = strip_think(entry)          # 清理模板泄漏
    with self._append_lock:               # 线程锁保证原子性
        cursor = self._next_cursor()      # 自增游标
        record = {"cursor": cursor, "timestamp": ts, "content": content}
        if session_key:
            record["session_key"] = session_key
        with open(self.history_file, "a") as f:
            f.write(json.dumps(record) + "\n")
        self._cursor_file.write_text(str(cursor))
    return cursor
```

关键设计：

- 追加写入：不修改已有行，只 append
- 游标递增：`.cursor` 文件记录最新游标，读取时可以只读未处理的条目
- `strip_think`：清理 `<think>` 标签等模板泄漏
- 硬上限：单条记录最大 64K 字符

读取 — `read_unprocessed_history(since_cursor)`：

```python
def read_unprocessed_history(self, since_cursor: int) -> list[dict]:
    return [e for c, e in self._iter_valid_entries() if c > since_cursor]
```

只返回游标大于 `since_cursor` 的条目，实现增量读取。

压缩 — `compact_history()`：

```python
def compact_history(self):
    entries = self._read_entries()
    if len(entries) > self.max_history_entries:   # 默认 1000
        kept = entries[-self.max_history_entries:]
        self._write_entries(kept)                 # 原子写入（tmp + fsync + rename）
```

### 1.3 Dream 游标

独立于历史游标，Dream 有自己的 `.dream_cursor`：

```python
def get_last_dream_cursor(self) -> int: ...
def set_last_dream_cursor(self, cursor: int) -> None: ...
```

Dream 是一个特殊的"后台思考"机制，用未处理的历史条目生成 prompt，让 LLM 自我反思并更新 MEMORY.md / SOUL.md。

### 1.4 Legacy 迁移

自动检测旧版 HISTORY.md，一次性迁移到 history.jsonl 格式，备份原文件。

## 二、Consolidator — LLM 驱动的历史压缩

当 session 的 token 数超过上下文窗口时，自动将旧消息压缩成摘要。

### 2.1 触发条件

```python
# memory.py: maybe_consolidate_by_tokens()
budget = context_window_tokens - max_completion_tokens - 1024
target = budget * consolidation_ratio   # 默认 0.5

if estimated > budget:
    # 循环压缩，最多 5 轮
    for round in range(5):
        if estimated <= target:
            break
        boundary = pick_consolidation_boundary(session, estimated - target)
        chunk = session.messages[last_consolidated:boundary]
        summary = await archive(chunk)   # LLM 摘要
```

### 2.2 archive() — 核心压缩方法

```python
async def archive(self, messages, *, session_key=None):
    formatted = self._format_messages(messages)       # 格式化为可读文本
    formatted = self._truncate_to_token_budget(formatted)

    response = await self.provider.chat_with_retry(    # 调用 LLM 生成摘要
        messages=[
            {"role": "system", "content": render_template("agent/consolidator_archive.md")},
            {"role": "user", "content": formatted},
        ],
    )
    summary = response.content
    self.store.append_history(summary, session_key=session_key)  # 写入历史
    return summary
```

如果 LLM 调用失败，降级为 `raw_archive()` — 直接 dump 原始消息。

### 2.3 消息边界选择

```python
def pick_consolidation_boundary(self, session, tokens_to_remove):
    for idx in range(start, len(session.messages)):
        if idx > start and message["role"] == "user":   # 只在 user 消息处切割
            last_boundary = (idx, removed_tokens)
            if removed_tokens >= tokens_to_remove:
                return last_boundary
```

总是在 user 消息边界切割，保证不会把一个 assistant 回复拆成两半。

## 三、AutoCompact — 空闲 session 自动压缩

```python
# autocompact.py
class AutoCompact:
    def check_expired(self, schedule_background, active_session_keys):
        for info in sessions.list_sessions():
            if is_expired(info["updated_at"]):     # TTL 过期
                schedule_background(self._archive(key))

    async def _archive(self, key):
        summary = await consolidator.compact_idle_session(key)
        # 保留最近 8 条消息，其余压缩
```

在 `AgentLoop.run()` 的主循环中，每次消息超时都会调用 `check_expired`，对长时间不活跃的 session 自动压缩，释放 token 空间。

## 四、注入到 LLM 系统提示

```python
# context.py: build_system_prompt()
parts = [identity, bootstrap_files, tool_contract]

# 1. 长期记忆
memory = self.memory.get_memory_context()  # 读 MEMORY.md
parts.append(f"# Memory\n\n{memory}")

# 2. 最近历史（增量）
entries = self.memory.read_recent_history_for_prompt(
    since_cursor=self.memory.get_last_dream_cursor()
)
parts.append("# Recent History\n\n" + history_text)

# 3. Session 摘要（AutoCompact 产生的）
if session_summary:
    parts.append(f"[Archived Context Summary]\n\n{session_summary}")
```

LLM 每次对话都能看到：长期记忆 + 最近的跨 session 历史 + 当前 session 的压缩摘要。

## 五、GitStore — 版本控制

```python
self._git = GitStore(workspace, tracked_files=[
    "SOUL.md", "USER.md", "memory/MEMORY.md", "memory/.dream_cursor",
])
```

用 dulwich（纯 Python Git 实现）对关键记忆文件做版本控制：

- `auto_commit(message)` — 有变更时自动提交
- `log()` / `diff_commits()` / `find_commit()` — 查看历史
- `revert(commit)` — 回滚到某个版本
- `line_ages(file)` — git blame 计算每行"年龄"

Dream 运行完成后会自动 `auto_commit("dream: ...")`，记录 AI 自我反思的结果。

## 六、完整数据流

```text
用户对话
  │
  ▼
AgentLoop._process_message()
  │
  ├─ Context Builder 读取:
  │    ├─ MEMORY.md（长期记忆）
  │    ├─ history.jsonl（最近历史，增量）
  │    └─ session summary（AutoCompact 摘要）
  │    → 注入到系统提示
  │
  ▼
LLM 对话 → session.messages 累积
  │
  ├─ 对话结束后: append_history() → history.jsonl（追加）
  │
  ├─ token 超限: Consolidator.maybe_consolidate_by_tokens()
  │    → LLM 摘要旧消息 → append_history(summary)
  │    → session.last_consolidated 前移
  │
  ├─ session 空闲: AutoCompact.check_expired()
  │    → compact_idle_session() → 压缩 + 保留最近 8 条
  │
  └─ Dream 定时运行:
       → build_dream_prompt() 读未处理历史
       → LLM 反思 → 更新 MEMORY.md / SOUL.md
       → GitStore.auto_commit()
```

## 总结

MemoryStore 是一个基于文件的分层记忆系统——MEMORY.md 存长期事实，history.jsonl 追加式存对话历史（带游标增量读取），SOUL.md/USER.md 存身份信息。Consolidator 在 token 超限时用 LLM 压缩旧消息，AutoCompact 对空闲 session 做自动清理，Dream 机制则让 AI 定期"反思"历史并更新长期记忆，所有关键文件通过 GitStore 做版本控制。

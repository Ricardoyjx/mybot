"""Shell 工具 — 在沙箱内执行终端命令。

安全限制：
- 工作目录限制在 workspace 内
- 禁止危险命令（rm -rf /, mkfs, dd, fork bomb 等）
- 命令超时 30 秒
- 输出截断 8000 字符
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from mybot.agent.tools.base import Tool, tool_parameters
from mybot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema

_DEFAULT_TIMEOUT = 30  # 秒
_MAX_OUTPUT_CHARS = 8000

# 危险命令模式（正则）
_DANGEROUS_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*f|--force)\b",        # rm -f / rm --force
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\b",        # rm -rf
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r\b",        # rm -fr
    r"\bmkfs\b",                                 # 格式化
    r"\bdd\s+if=",                               # dd 写盘
    r":\(\)\{.*\}",                              # fork bomb
    r"\bshutdown\b",                             # 关机
    r"\breboot\b",                               # 重启
    r"\bkill\s+-9\s+1\b",                        # kill init
    r"\bchmod\s+-R\s+777\s+/",                   # 危险 chmod
    r"\bwget\b.*\|\s*bash",                      # 下载执行
    r"\bcurl\b.*\|\s*(bash|sh)",                 # 下载执行
    r">\s*/dev/sd",                              # 写磁盘
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_PATTERNS]


def _is_dangerous(command: str) -> str | None:
    """检查命令是否危险。返回危险原因，或 None 表示安全。"""
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(command):
            return f"命令匹配危险模式: {pattern.pattern}"
    return None


@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("要执行的 shell 命令"),
        timeout=IntegerSchema(
            _DEFAULT_TIMEOUT,
            description="超时秒数（默认 30）",
            minimum=5,
            maximum=120,
        ),
        required=["command"],
    )
)
class ShellTool(Tool):
    """在沙箱内执行终端命令并返回输出。"""

    _scopes = {"core"}
    name = "shell"
    description = (
        "执行终端命令并返回 stdout 和 stderr。"
        "适用于运行脚本、查看系统信息、安装依赖等。"
        "有安全限制：禁止危险命令，超时 30 秒。"
    )

    @classmethod
    def create(cls, ctx: Any) -> "ShellTool":
        from mybot.agent.tools.context import ToolContext
        workspace = getattr(ctx, "workspace", ".")
        return cls(workspace=Path(workspace))

    def __init__(self, workspace: Path | None = None, timeout: int = _DEFAULT_TIMEOUT):
        self.workspace = workspace or Path.cwd()
        self.timeout = timeout

    async def execute(
        self,
        command: str,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> str:
        # 安全检查
        danger = _is_dangerous(command)
        if danger:
            logger.warning("ShellTool: 拒绝危险命令 '{}': {}", command, danger)
            return f"❌ 命令被拒绝: {danger}"

        timeout = timeout or self.timeout
        logger.info("ShellTool: 执行 '{}', timeout={}s", command, timeout)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace),
                env={**os.environ, "MYBOT_SHELL": "1"},
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("ShellTool: 命令超时 ({}s)", timeout)
                return f"⏰ 命令超时 ({timeout}s)，已终止"

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            parts = []
            if stdout_text:
                parts.append(stdout_text)
            if stderr_text:
                parts.append(f"[stderr]\n{stderr_text}")
            if proc.returncode != 0:
                parts.append(f"[exit code: {proc.returncode}]")

            output = "\n".join(parts) if parts else "(无输出)"

            # 截断
            if len(output) > _MAX_OUTPUT_CHARS:
                output = output[:_MAX_OUTPUT_CHARS] + "\n...(截断)"

            logger.debug("ShellTool: 完成, exit={}, output_len={}", proc.returncode, len(output))
            return output

        except Exception as e:
            logger.error("ShellTool: 执行失败: {}", e)
            return f"❌ 执行失败: {e}"

from tkinter import E
from typing import Any

from mybot.agent.tools.registry import ToolRegistry
from loguru import logger


class ToolLoader:

    def loader(
        self, ctx: Any, registry: ToolRegistry, *, scope: str = "core"
    ) -> list[str]:
        registered: list[str] = []
        builtin_names = set[str] = set()

        # 多源统一处理 构建一个包含“数据源”和“来源标识”的元组列表
        sources = [(self.discover(), False), (self._discover_plugins().values(), True)]
        for source, is_plugin_source in sources:
            for tool_cls in source:
                cls_label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}):
                        continue
                    if not tool_cls.enable(ctx):
                        continue
                    tool = tool_cls.create(ctx)
                    if registry.has(tool.name):
                        # 内置工具绝对优先：如果当前正在处理的是插件（is_plugin_source=True），
                        # 且它的名字和已经注册的内置工具（builtin_names）重名，
                        # 插件会被直接跳过（Skipped）并记录警告。这防止了第三方插件意外覆盖系统核心功能。
                        # 允许普通覆盖：如果是内置工具之间的冲突，或者非内置工具之间的冲突，
                        # 则允许覆盖（Overwrites），并记录警告日志。
                        if is_plugin_source and tool.name in builtin_names:
                            logger.warning(
                                "Plugin %s skipped: conflicts with built-in tool %s",
                                cls_label,
                                tool.name,
                            )
                            continue
                        logger.warning(
                            "Tool name collision: %s from %s overwrites existing",
                            tool.name,
                            cls_label,
                        )
                    registry.register(tool)
                    registered.append(tool.name)
                    if not is_plugin_source:
                        builtin_names.add(tool.name)
                except Exception:
                    logger.exception("Failed to register tool: %s", cls_label)
        return registered

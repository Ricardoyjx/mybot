from __future__ import annotations

import pkgutil
from typing import Any

from loguru import logger

import mybot.agent.tools as tools_pkg
from mybot.agent.tools.base import Tool
from mybot.agent.tools.registry import ToolRegistry


class ToolLoader:
    """Discover and register built-in + plugin tools."""

    def discover(self) -> list[type[Tool]]:
        """Scan mybot.agent.tools package for Tool subclasses."""
        found: list[type[Tool]] = []
        for _importer, modname, _ispkg in pkgutil.iter_modules(
            tools_pkg.__path__, prefix=f"{tools_pkg.__name__}."
        ):
            try:
                import importlib

                mod = importlib.import_module(modname)
            except Exception:
                logger.warning("ToolLoader: failed to import {}", modname)
                continue
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, Tool)
                    and obj is not Tool
                    and not getattr(obj, "__abstractmethods__", None)
                    and getattr(obj, "_plugin_discoverable", True)
                ):
                    found.append(obj)
        return found

    def _discover_plugins(self) -> dict[str, type[Tool]]:
        """Discover third-party plugin tools via entry points (placeholder)."""
        return {}

    def load(
        self, ctx: Any, registry: ToolRegistry, *, scope: str = "core"
    ) -> list[str]:
        registered: list[str] = []
        builtin_names: set[str] = set()

        sources = [(self.discover(), False), (self._discover_plugins().values(), True)]
        for source, is_plugin_source in sources:
            for tool_cls in source:
                cls_label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}):
                        continue
                    if not tool_cls.enabled(ctx):
                        continue
                    tool = tool_cls.create(ctx)
                    if registry.has(tool.name):
                        if is_plugin_source and tool.name in builtin_names:
                            logger.warning(
                                "Plugin {} skipped: conflicts with built-in tool {}",
                                cls_label,
                                tool.name,
                            )
                            continue
                        logger.warning(
                            "Tool name collision: {} from {} overwrites existing",
                            tool.name,
                            cls_label,
                        )
                    registry.register(tool)
                    registered.append(tool.name)
                    if not is_plugin_source:
                        builtin_names.add(tool.name)
                except Exception:
                    logger.exception("Failed to register tool: {}", cls_label)
        return registered

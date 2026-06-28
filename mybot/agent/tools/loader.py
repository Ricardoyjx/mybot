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
        logger.debug("ToolLoader: scanning {}", tools_pkg.__path__)
        for _importer, modname, _ispkg in pkgutil.iter_modules(
            tools_pkg.__path__, prefix=f"{tools_pkg.__name__}."
        ):
            try:
                import importlib

                mod = importlib.import_module(modname)
                logger.debug("ToolLoader: imported module {}", modname)
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
                    logger.debug("ToolLoader: discovered tool class {}", obj.__name__)
                    found.append(obj)
        logger.info("ToolLoader: discovered {} builtin tool classes", len(found))
        return found

    def _discover_plugins(self) -> dict[str, type[Tool]]:
        """Discover third-party plugin tools via entry points (placeholder)."""
        logger.debug("ToolLoader: plugin discovery not yet implemented")
        return {}

    def load(
        self, ctx: Any, registry: ToolRegistry, *, scope: str = "core"
    ) -> list[str]:
        registered: list[str] = []
        builtin_names: set[str] = set()

        sources = [(self.discover(), False), (self._discover_plugins().values(), True)]
        for source, is_plugin_source in sources:
            source_label = "plugin" if is_plugin_source else "builtin"
            for tool_cls in source:
                cls_label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}):
                        logger.debug(
                            "ToolLoader: skipping {} (scope mismatch, needs {})",
                            cls_label,
                            getattr(tool_cls, "_scopes", set()),
                        )
                        continue
                    if not tool_cls.enabled(ctx):
                        logger.debug("ToolLoader: skipping {} (disabled by config)", cls_label)
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
                    logger.debug(
                        "ToolLoader: registered [{}] {} -> {}",
                        source_label,
                        cls_label,
                        tool.name,
                    )
                except Exception:
                    logger.exception("Failed to register tool: {}", cls_label)
        logger.info("ToolLoader: load complete, {} tools registered", len(registered))
        return registered

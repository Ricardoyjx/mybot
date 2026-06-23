from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class _FileConfig:
    enable: bool = True


@dataclass
class _MyConfig:
    enable: bool = True


@dataclass
class _ExecConfig:
    sandbox: bool = False


@dataclass
class _ToolsConfig:
    file: _FileConfig = field(default_factory=_FileConfig)
    my: _MyConfig = field(default_factory=_MyConfig)
    exec: _ExecConfig = field(default_factory=_ExecConfig)


@dataclass
class ToolContext:
    """Runtime context passed to tool factories and enabled() checks."""

    config: _ToolsConfig = field(default_factory=_ToolsConfig)
    workspace: str = "."
    restrict_to_workspace: bool = False

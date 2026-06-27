"""Configuration schema using Pydantic."""

from __future__ import annotations


from typing import Literal

from pydantic import Field


from mybot.config_base import Base


class Config:
    pass


class InlineFallbackConfig:
    pass


class ModelPresetConfig(Base):
    pass


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = (
        None  # auto-detected if omitted
    )
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    cwd: str = ""  # Stdio: working directory for MCP server runtime artifacts
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers
    tool_timeout: int = 30  # seconds before a tool call is cancelled
    enabled_tools: list[str] = Field(
        default_factory=lambda: ["*"]
    )  # Only register these tools; accepts raw MCP names or wrapped mcp_<server>_<tool> names; ["*"] = all tools; [] = no tools


class ProviderConfig(Base):
    pass


class ToolsConfig(Base):
    pass


class ChannelConfig(Base):
    pass

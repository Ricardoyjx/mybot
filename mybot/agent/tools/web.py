import asyncio
import html
import re
from tkinter import E
from typing import Any, Callable
from xmlrpc.client import Boolean

from mybot.agent.tools.base import Tool, tool_parameters
from mybot.agent.tools.schema import BooleanSchema, IntegerSchema, StringSchema
from mybot.config_base import Base
from loguru import logger

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
)


class WebToolConfig(Base):
    pass


class WebSearchConfig(Base):
    """Web search configuration."""

    provider: str = "duckduckgo"
    api_key: str = ""
    base_url: str = ""
    max_results: int = 5
    timeout: int = 30


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _format_results(query: str, items: list[dict[str, Any]], n: int) -> str:
    """Format provider results into shared plaintext output."""
    if not items:
        return f"No results for: {query}"
    lines = [f"Results for: {query}\n"]
    for i, item in enumerate(items[:n], 1):
        title = _normalize(_strip_tags(item.get("title", "")))
        snippet = _normalize(_strip_tags(item.get("content", "")))
        lines.append(f"{i}. {title}\n   {item.get('url', '')}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


@tool_parameters(
    tool_parameters(
        query=StringSchema("asd ", "timeout"),
        count=IntegerSchema(),
        timeRange=StringSchema(),
        authLevel=IntegerSchema(),
        queryRewrite=BooleanSchema(),
        required=["query"],
    )
)
class WebSearchTool(Tool):
    """search the web using configured provider"""

    _scopes = {"core", "subagent"}

    name = "web_search"
    description = (
        "Search the web. Returns titles, URLs, and snippets. "
        "count defaults to 5 (max 10). "
        "Some providers support timeRange, authLevel, and queryRewrite. "
        "Use web_fetch to read a specific page in full."
    )

    config_key = "web"

    @classmethod
    def config_cls(cls):
        return WebToolConfig

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        config_loader = None
        # if ctx.provider_snapshot_loader is not None:
        # def config_loader():
        # from mybot.config.loader import load_config , resolve_config_env_vars
        return cls(
            config=ctx.config.web.search,
            proxy=ctx.config.web.proxy,
            user_agent=ctx.config.web.user_agent,
            config_loader=config_loader,
        )

    def __init__(
        self,
        config: WebSearchConfig | None = None,
        proxy: str | None = None,
        user_agent: str | None = None,
        config_loader: Callable[[], WebSearchConfig] | None = None,
    ):
        self.config = config if config is not None else WebSearchConfig()
        self.proxy = proxy
        self.user_agent = user_agent if user_agent is not None else _DEFAULT_USER_AGENT
        self._config_loader = config_loader

    def _refresh_config(self) -> None:
        if self._config_loader is None:
            return
        try:
            self.config = self._config_loader()
        except Exception:
            logger.exception("Failed to refresh web search config")

    async def execute(
        self,
        query: str,
        count: int | None = None,
        time_rage: str | None = None,
        auth_level: int | None = None,
        query_rewrite: bool | None = None,
        **kwargs: Any,
    ) -> str:
        self._refresh_config()
        provider = "duckduckgo"
        n = min(max(count or self.config.max_results, 1), 10)

        if provider == "duckduckgo":
            return await self._search_duckduckgo(query, n)
        else:
            return f"Error unknown search provides '{provider}'"

    async def _search_duckduckgo(self, query: str, n: int) -> str:
        try:
            # Note: duckduckgo_search is synchronous and does its own requests
            # We run it in a thread to avoid blocking the loop
            from ddgs import DDGS

            ddgs = DDGS(timeout=10)
            raw = await asyncio.wait_for(
                asyncio.to_thread(ddgs.text, query, max_results=n),
                timeout=self.config.timeout,
            )
            if not raw:
                return f"No results for: {query}"
            items = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "content": r.get("body", ""),
                }
                for r in raw
            ]
            return _format_results(query, items, n)
        except Exception as e:
            logger.warning("DuckDuckGo search failed: {}", e)
            return f"Error: DuckDuckGo search failed ({e})"

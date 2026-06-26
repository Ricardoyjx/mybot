import asyncio
import html
import re
from tkinter import E
from typing import Any, Callable
from xmlrpc.client import Boolean

from mybot.agent.tools.base import Tool, tool_parameters
from mybot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
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
    tool_parameters_schema(
        query=StringSchema("Search query"),
        count=IntegerSchema(1, description="Results (1-10)", minimum=1, maximum=10),
        timeRange=StringSchema(
            "Optional time filter for providers that support it: "
            "OneDay, OneWeek, OneMonth, OneYear, or YYYY-MM-DD..YYYY-MM-DD",
        ),
        authLevel=IntegerSchema(
            0,
            description="Optional authority filter for providers that support it: 0=all, 1=authoritative",
            minimum=0,
            maximum=1,
        ),
        queryRewrite=BooleanSchema(
            description="Optional provider-side query rewrite for conversational or ambiguous searches",
        ),
        required=["query"],
    )
)
class WebSearchTool(Tool):
    """search the web using configured provider"""

    _scopes = {"core", "subagent"}

    name = "web_search"
    description = (
        "搜索互联网获取实时信息。适用于查询天气、新闻、最新资讯等任何需要联网获取的信息。"
        "返回标题、URL 和摘要。count 默认 5（最大 10）。"
        "当用户询问天气、新闻、实时数据等联网问题时必须使用此工具。"
    )

    config_key = "web"

    @classmethod
    def config_cls(cls):
        return WebToolConfig

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        # config_loader = None
        # if ctx.provider_snapshot_loader is not None:
        # def config_loader():
        # from mybot.config.loader import load_config , resolve_config_env_vars
        return cls()

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
        time_range: str | None = None,
        auth_level: int | None = None,
        query_rewrite: bool | None = None,
        **kwargs: Any,
    ) -> str:
        self._refresh_config()
        provider = "duckduckgo"
        n = min(max(count or self.config.max_results, 1), 10)
        logger.info("WebSearch: query='{}', provider={}, count={}", query, provider, n)

        if provider == "duckduckgo":
            return await self._search_duckduckgo(query, n)
        else:
            logger.error("WebSearch: unknown provider '{}'", provider)
            return f"Error unknown search provides '{provider}'"

    async def _search_duckduckgo(self, query: str, n: int) -> str:
        try:
            from ddgs import DDGS

            timeout = min(self.config.timeout, 10)
            logger.info(
                "WebSearch: starting DuckDuckGo search, timeout={}s",
                timeout,
            )

            def _sync_search():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=n))

            raw = await asyncio.wait_for(
                asyncio.to_thread(_sync_search),
                timeout=timeout,
            )
            if not raw:
                logger.warning("WebSearch: no results for '{}'", query)
                return f"No results for: {query}"
            items = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "content": r.get("body", ""),
                }
                for r in raw
            ]
            logger.info("WebSearch: got {} results for '{}'", len(items), query)
            return _format_results(query, items, n)
        except asyncio.TimeoutError:
            logger.warning(
                "WebSearch: DuckDuckGo timed out after {}s for '{}'",
                timeout,
                query,
            )
            return f"Error: Search timed out ({timeout}s). Try a more specific query."
        except Exception as e:
            logger.warning("WebSearch: DuckDuckGo search failed: {}", e)
            return f"Error: Search failed ({type(e).__name__}: {e})"

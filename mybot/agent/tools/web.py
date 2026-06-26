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
        n = min(max(count or self.config.max_results, 1), 10)
        logger.info("WebSearch: query='{}', count={}", query, n)
        return await self._search_bing(query, n)

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

    async def _search_bing(self, query: str, n: int) -> str:
        """Fallback: scrape Bing search results via httpx."""
        import httpx
        from lxml import html as lxml_html

        try:
            logger.info("WebSearch: trying Bing fallback for '{}'", query)
            async with httpx.AsyncClient(
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            ) as client:
                resp = await client.get(
                    "https://cn.bing.com/search",
                    params={"q": query, "count": str(n)},
                )
                resp.raise_for_status()

            tree = lxml_html.fromstring(resp.text)
            items = []
            for li in tree.xpath('//li[@class="b_algo"]')[:n]:
                title_parts = li.xpath('.//h2//text()')
                title = " ".join(title_parts).strip()
                href_list = li.xpath('.//h2/a/@href')
                href = href_list[0] if href_list else ""
                snippet_parts = li.xpath('.//p//text()')
                snippet = " ".join(snippet_parts).strip()
                if title:
                    items.append({"title": title, "url": href, "content": snippet})

            if not items:
                logger.warning("WebSearch: Bing returned no parseable results for '{}'", query)
                return f"No results for: {query}"

            logger.info("WebSearch: Bing got {} results for '{}'", len(items), query)
            return _format_results(query, items, n)
        except Exception as e:
            logger.warning("WebSearch: Bing fallback failed: {}", e)
            return f"Error: All search providers failed ({type(e).__name__}: {e})"


@tool_parameters(
    tool_parameters_schema(
        url=StringSchema("要读取的网页 URL"),
        max_chars=IntegerSchema(
            8000, description="最大返回字符数", minimum=1000, maximum=50000
        ),
        required=["url"],
    )
)
class WebFetchTool(Tool):
    """Fetch a web page and extract its text content."""

    _scopes = {"core", "subagent"}
    _plugin_discoverable = True

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "抓取网页内容并提取纯文本。用于读取搜索结果中的具体页面。"
            "传入 URL，返回页面的文本内容。"
            "配合 web_search 使用：先搜索获取 URL，再用此工具读取页面详情。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要读取的网页 URL"},
                "max_chars": {
                    "type": "integer",
                    "description": "最大返回字符数",
                    "default": 8000,
                },
            },
            "required": ["url"],
        }

    @classmethod
    def create(cls, ctx: Any) -> "WebFetchTool":
        return cls()

    async def execute(
        self, url: str, max_chars: int = 8000, **kwargs: Any
    ) -> str:
        import httpx
        from lxml import html as lxml_html

        logger.info("WebFetch: fetching {}", url)
        try:
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            # Parse HTML and extract text
            tree = lxml_html.fromstring(resp.text)

            # Remove script and style elements
            for tag in tree.xpath("//script | //style | //nav | //footer | //header"):
                tag.getparent().remove(tag)

            # Try to find the main content area
            main = tree.xpath("//main | //article | //div[@class='content'] | //div[@id='content']")
            if main:
                text = main[0].text_content()
            else:
                text = tree.text_content()

            # Clean up whitespace
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()

            if len(text) > max_chars:
                text = text[:max_chars] + "\n...(truncated)"

            if not text:
                logger.warning("WebFetch: empty content from {}", url)
                return f"Error: Page returned empty content ({url})"

            logger.info("WebFetch: got {} chars from {}", len(text), url)
            return text

        except httpx.TimeoutException:
            logger.warning("WebFetch: timed out fetching {}", url)
            return f"Error: Timed out fetching {url}"
        except Exception as e:
            logger.warning("WebFetch: failed to fetch {}: {}", url, e)
            return f"Error: Failed to fetch {url} ({type(e).__name__}: {e})"

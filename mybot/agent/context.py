from agent.tools import mcp as mcp_tools
from agent.tools.registry import ToolRegistry


async def connect_mcp(state: any, tools: ToolRegistry) -> None:
    return await mcp_tools.connect_missing_servers(state, tools)

from tool.tool import Tool
from typing import Any

def _normalize_schema_for_openai(schema: any)-> dict[str,Any]:
    """Normalize only nullable JSON Schema patterns for tool definitions."""
    if not isinstance(schema,dict):
        return {"type":"object","properties":{}}
    normalized = dict(schema)

    raw_type = normalized.get("type")

class _MCPWrapperBase(Tool):
    pass

class MCPToolWrapper(_MCPWrapperBase):
    """将远程mcp调用包装为本地工具接口"""
    def __init__(self,server_name,tool_spec):
        self.name = f"mcp_{server_name}_{tool_spec.name}"
        self.description = tool_spec.description
        self.schema = self._normalize_schema(tool_spec.input_schema)

    async def run (self,args):
        # 将本地调用转换为 MCP 协议的远程调用
        result = await self.mcp_client.call_tool(
            self.original_name,args
        )
        return result
    
    def _normalize_schema(self,schema):
        """将 MCP schema 转换为 OpenAI 兼容格式"""
        return self._normalize_schema
import asyncio
import json
from typing import Any
from uuid import UUID

import psycopg
from fastapi.encoders import jsonable_encoder
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError

from app import knowledge
from app.errors import AppError
from app.retrieval.search import search
from app.schemas import SearchRequest


def create_mcp(runtime):
    mcp = FastMCP('rag-kb', streamable_http_path='/', stateless_http=True, json_response=True)
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    async def invoke(fn, *args):
        try:
            if not runtime.ready:
                raise AppError("SERVICE_NOT_READY", runtime.error or "服务正在初始化", 503)
            return jsonable_encoder(await asyncio.to_thread(fn, *args))
        except AppError as exc:
            payload = exc.payload()
        except (ValidationError, ValueError):
            payload = AppError("INVALID_ARGUMENT", "参数无效", 422).payload()
        except psycopg.Error:
            payload = AppError("DATABASE_UNAVAILABLE", "数据库不可用", 503, True).payload()
        return CallToolResult(isError=True, content=[TextContent(type='text', text=json.dumps(payload,
                                                                                           ensure_ascii=False))])

    @mcp.tool(annotations=annotations)
    async def list_knowledge_bases(limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """列出知识库及 ready 文档数；total 大于已读数量时继续翻页。"""
        def execute():
            if not 1 <= limit <= 100 or offset < 0:
                raise AppError("INVALID_ARGUMENT", "分页参数无效", 422)
            return knowledge.list_kbs(runtime.db, limit, offset)
        return await invoke(execute)

    @mcp.tool(annotations=annotations)
    async def search_knowledge_base(kb_id: str, query: str, mode: str = 'hybrid', top_k: int = 5,
                                   allow_partial: bool = False, debug: bool = False) -> dict[str, Any]:
        """限定指定 kb_id 检索。mode: vector/keyword/graph/hybrid；top_k 1..20。
        supporting_results 补齐图路径证据，不计入 top_k。错误不是无结果；回答必须引用返回的来源。
        """
        def execute():
            request = SearchRequest(query=query, mode=mode, top_k=top_k, allow_partial=allow_partial, debug=debug)
            return search(runtime.db, runtime.settings, runtime.encoder, UUID(kb_id), request)
        return await invoke(execute)
    return mcp

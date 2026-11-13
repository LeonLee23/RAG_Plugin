"""Thin stdio adapter. No retrieval or model logic lives here."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import ValidationError

from app import ingestion, knowledge
from app.errors import AppError
from app.retrieval import search
from app.schemas import Enrichment


def create_mcp(settings):
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rag-ingestion')

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    server = FastMCP('RAGPlugin', lifespan=lifespan)
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

    async def invoke(fn, **kwargs):
        request_id = uuid4().hex
        try:
            result = await asyncio.to_thread(fn, settings, **kwargs)
            picture = result.pop('_image', None)
            result = {'request_id': request_id, 'warnings': [], **result}
            content = [TextContent(type='text', text=json.dumps(result, ensure_ascii=False))]
            if picture:
                content.append(ImageContent(**picture))
            return CallToolResult(content=content, structuredContent=result)
        except AppError as exc:
            error = exc
        except (ValidationError, ValueError, TypeError):
            error = AppError('INVALID_ARGUMENT', '参数或内容格式无效')
        except OSError as exc:
            error = AppError('IO_ERROR', f'文件操作失败：{type(exc).__name__}', retryable=True)
        except Exception:
            logging.exception('Unhandled RAGPlugin error')
            error = AppError('INTERNAL_ERROR', '内部错误，请检查 stderr 日志')
        payload = error.payload(request_id)
        return CallToolResult(isError=True, content=[TextContent(type='text', text=json.dumps(payload,
                              ensure_ascii=False))], structuredContent=payload)

    def background(kb_id, task_id):
        try:
            ingestion.run_task(settings, kb_id, task_id)
        except AppError as exc:
            logging.info('Ingestion stopped: %s', exc.code)
        except Exception:
            logging.exception('Ingestion interrupted; explicit resume required')

    @server.tool(annotations=write)
    async def create_knowledge_base(path: str, name: str, operation_id: str, description: str = ''):
        """管理：在授权的新/空目录建库，注册读写挂载。"""
        return await invoke(knowledge.create_knowledge_base, path=path, name=name,
                            operation_id=operation_id, description=description)

    @server.tool(annotations=write)
    async def mount_knowledge_base(path: str, access: str = 'read_only', replace: bool = False):
        """管理：挂载已有文件包；默认只读。replace 显式替换同 ID 的旧路径。"""
        return await invoke(knowledge.mount_knowledge_base, path=path, access=access, replace=replace)

    @server.tool(annotations=write)
    async def unmount_knowledge_base(kb_id: str):
        """管理：取消本机挂载，不删除文件；活动任务须先完成/取消。"""
        return await invoke(knowledge.unmount_knowledge_base, kb_id=kb_id)

    @server.tool(annotations=read)
    async def list_knowledge_bases(cursor: str | None = None, limit: int = 20):
        """只读：分页列出已挂载库、访问模式及离线错误。"""
        return await invoke(knowledge.list_knowledge_bases, cursor=cursor, limit=limit)

    @server.tool(annotations=read)
    async def list_documents(kb_id: str, cursor: str | None = None, limit: int = 20,
                             expected_kb_revision: int | None = None):
        """只读：分页列出已发布文档、索引有效性和更新任务。"""
        return await invoke(knowledge.list_documents, kb_id=kb_id, cursor=cursor, limit=limit,
                            expected_kb_revision=expected_kb_revision)

    @server.tool(annotations=write)
    async def import_document(kb_id: str, source_path: str, operation_id: str, doc_id: str | None = None,
                              expected_doc_revision: str | None = None, resume: bool = False):
        """管理：新增/更新或显式恢复入库。返回任务后用 get_ingestion_task 查询；图片由当前宿主处理。"""
        result = await invoke(ingestion.import_document, kb_id=kb_id, source_path=source_path,
                              operation_id=operation_id, doc_id=doc_id,
                              expected_doc_revision=expected_doc_revision, resume=resume)
        if not result.isError and result.structuredContent.get('state') == 'queued':
            executor.submit(background, kb_id, result.structuredContent['task_id'])
        return result

    @server.tool(annotations=destructive)
    async def delete_document(kb_id: str, doc_id: str, expected_doc_revision: str, operation_id: str):
        """管理：逻辑删除指定版本并使旧任务失效；物理文件保留。"""
        return await invoke(knowledge.delete_document, kb_id=kb_id, doc_id=doc_id,
                            expected_doc_revision=expected_doc_revision, operation_id=operation_id)

    @server.tool(annotations=read)
    async def get_ingestion_task(kb_id: str, task_id: str):
        """只读：查看状态、attempt、错误、待处理图片及发布回执，不自动恢复。"""
        return await invoke(ingestion.get_ingestion_task, kb_id=kb_id, task_id=task_id)

    @server.tool(annotations=read)
    async def read_ingestion_input(kb_id: str, task_id: str, attempt: int, item_id: str,
                                   cursor: str | None = None):
        """只读：当前任务的节点正文或真实 MCP 图像；返回 source_sha256 供派生提交，不是已发布证据。"""
        return await invoke(ingestion.read_ingestion_input, kb_id=kb_id, task_id=task_id,
                            attempt=attempt, item_id=item_id, cursor=cursor)

    @server.tool(annotations=write)
    async def submit_enrichment(kb_id: str, task_id: str, attempt: int, items: list[Enrichment]):
        """管理：提交实际读图后的描述或可选摘要。图片使用 read_ingestion_input 返回的图片哈希。"""
        return await invoke(ingestion.submit_enrichment, kb_id=kb_id, task_id=task_id, attempt=attempt,
                            items=[i.model_dump() for i in items])

    @server.tool(annotations=write)
    async def publish_document(kb_id: str, task_id: str, attempt: int, expected_doc_revision: str | None = None):
        """管理：完成图片处理后原子发布；更新必须再次携带预期旧文档版本。"""
        return await invoke(ingestion.publish_document, kb_id=kb_id, task_id=task_id, attempt=attempt,
                            expected_doc_revision=expected_doc_revision)

    @server.tool(annotations=write)
    async def cancel_ingestion(kb_id: str, task_id: str, attempt: int):
        """管理：取消任务，使旧写回失效；已经发布的文档不回删。"""
        return await invoke(ingestion.cancel_ingestion, kb_id=kb_id, task_id=task_id, attempt=attempt)

    @server.tool(annotations=read)
    async def search_knowledge_base(kb_id: str, query: str, match: str = 'terms', doc_ids: list[str] | None = None,
                                    limit: int = 20, cursor: str | None = None,
                                    expected_kb_revision: int | None = None):
        """只读：词面 terms / 精确 exact 候选检索，不生成答案；片段须进一步读取为证据。"""
        return await invoke(search.search_knowledge_base, kb_id=kb_id, query=query, match=match, doc_ids=doc_ids,
                            limit=limit, cursor=cursor, expected_kb_revision=expected_kb_revision)

    @server.tool(annotations=read)
    async def get_document_outline(kb_id: str, doc_id: str, parent_node_id: str | None = None,
                                    cursor: str | None = None, limit: int = 20,
                                    expected_kb_revision: int | None = None):
        """只读：分页目录，传 parent_node_id 展开子节点；摘要仅导航。"""
        return await invoke(search.get_document_outline, kb_id=kb_id, doc_id=doc_id, parent_node_id=parent_node_id,
                            cursor=cursor, limit=limit, expected_kb_revision=expected_kb_revision)

    @server.tool(annotations=read)
    async def read_evidence(kb_id: str, doc_id: str, doc_revision: str, node_id: str, cursor: str | None = None,
                             expected_kb_revision: int | None = None):
        """只读：读取节点直属原文，每页最多 12000 字符；子章节须分别读取，回答前校验 evidence_id。"""
        return await invoke(search.read_evidence, kb_id=kb_id, doc_id=doc_id, doc_revision=doc_revision,
                            node_id=node_id, cursor=cursor, expected_kb_revision=expected_kb_revision)

    @server.tool(annotations=read)
    async def read_asset(kb_id: str, doc_id: str, doc_revision: str, asset_id: str,
                          expected_kb_revision: int | None = None):
        """只读：返回已发布真实图像、位置及派生描述，不能把图片描述冒充原文。"""
        return await invoke(search.read_asset, kb_id=kb_id, doc_id=doc_id, doc_revision=doc_revision,
                            asset_id=asset_id, expected_kb_revision=expected_kb_revision)

    @server.tool(annotations=read)
    async def validate_evidence(kb_id: str, expected_kb_revision: int, evidence_refs: list[str]):
        """只读：回答前校验读取工具返回的 evidence_id、版本与哈希；不判断语义支持。"""
        return await invoke(search.validate_evidence, kb_id=kb_id, expected_kb_revision=expected_kb_revision,
                            evidence_refs=evidence_refs)

    return server

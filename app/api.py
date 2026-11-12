from uuid import UUID

import psycopg
from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app import knowledge
from app.answering import answer
from app.config import MAX_UPLOAD
from app.db import get_kb
from app.errors import AppError
from app.retrieval.search import search
from app.schemas import AskRequest, KnowledgeBaseCreate, SearchRequest


def router(runtime):
    api = APIRouter(prefix='/api')

    def available():
        if not runtime.ready:
            raise AppError("SERVICE_NOT_READY", runtime.error or "服务正在初始化", 503, True)

    @api.get('/runtime')
    def status():
        counts = {}
        database_ready = False
        try:
            with runtime.db.connect() as conn:
                counts = {r['status']: r['n'] for r in conn.execute(
                    'SELECT status,count(*) AS n FROM documents GROUP BY status')}
                database_ready = True
        except psycopg.Error:
            pass
        return {"ready": runtime.ready, "error": runtime.error,
                "database_ready": database_ready, "document_counts": counts,
                "embedding_ready": runtime.ready and runtime.settings.embedding_base_url is not None,
                "provider": runtime.settings.agent_provider_1, "model": runtime.settings.agent_model,
                "providers": runtime.settings.agent_providers,
                "embedding_model": runtime.settings.embedding_model,
                "last_model_provider": runtime.agent.last_provider,
                "last_model_error": runtime.agent.last_error}

    @api.get('/knowledge-bases')
    def list_kbs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        available()
        return knowledge.list_kbs(runtime.db, limit, offset)

    @api.post('/knowledge-bases', status_code=201)
    def create_kb(body: KnowledgeBaseCreate):
        available()
        return knowledge.create_kb(runtime.db, runtime.settings, body)

    @api.get('/knowledge-bases/{kb_id}')
    def detail(kb_id: UUID):
        available()
        with runtime.db.connect() as conn:
            return get_kb(conn, kb_id)

    @api.delete('/knowledge-bases/{kb_id}', status_code=204)
    def delete_kb(kb_id: UUID):
        available()
        knowledge.delete_kb(runtime.db, runtime.settings, kb_id)

    @api.get('/knowledge-bases/{kb_id}/documents')
    def list_documents(kb_id: UUID, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        available()
        return knowledge.list_documents(runtime.db, kb_id, limit, offset)

    @api.post('/knowledge-bases/{kb_id}/documents')
    async def upload(kb_id: UUID, file: UploadFile = File(...)):
        available()
        content = bytearray()
        try:
            while chunk := await file.read(1024 * 1024):
                content.extend(chunk)
                if len(content) > MAX_UPLOAD:
                    raise AppError("FILE_TOO_LARGE", "单文件不得超过 20 MiB", 413)
            result = await run_in_threadpool(knowledge.upload_document, runtime.db, runtime.settings,
                                            kb_id, file.filename or '', bytes(content))
            return JSONResponse(jsonable_encoder(result), status_code=200 if result['deduplicated'] else 202)
        finally:
            await file.close()

    @api.get('/knowledge-bases/{kb_id}/documents/{doc_id}')
    def document(kb_id: UUID, doc_id: UUID):
        available()
        with runtime.db.connect() as conn:
            return knowledge.public_document(knowledge.document(conn, kb_id, doc_id))

    @api.post('/knowledge-bases/{kb_id}/documents/{doc_id}/retry', status_code=202)
    def retry(kb_id: UUID, doc_id: UUID):
        available()
        return knowledge.retry_document(runtime.db, kb_id, doc_id)

    @api.delete('/knowledge-bases/{kb_id}/documents/{doc_id}', status_code=204)
    def delete_document(kb_id: UUID, doc_id: UUID):
        available()
        knowledge.delete_document(runtime.db, runtime.settings, kb_id, doc_id)

    @api.get('/knowledge-bases/{kb_id}/documents/{doc_id}/file')
    def download(kb_id: UUID, doc_id: UUID):
        available()
        path, filename = knowledge.file_path(runtime.db, runtime.settings, kb_id, doc_id)
        return FileResponse(path, filename=filename)

    @api.get('/knowledge-bases/{kb_id}/documents/{doc_id}/assets/{asset_id}')
    def image(kb_id: UUID, doc_id: UUID, asset_id: str):
        available()
        path, _ = knowledge.file_path(runtime.db, runtime.settings, kb_id, doc_id, asset_id)
        return FileResponse(path, media_type='image/png')

    @api.post('/knowledge-bases/{kb_id}/search')
    def retrieve(kb_id: UUID, body: SearchRequest, request: Request):
        available()
        return search(runtime.db, runtime.settings, runtime.encoder, kb_id, body, request.state.request_id)

    @api.post('/knowledge-bases/{kb_id}/ask')
    def ask(kb_id: UUID, body: AskRequest, request: Request):
        available()
        return answer(runtime.db, runtime.settings, runtime.encoder, runtime.agent,
                      kb_id, body, request.state.request_id)

    return api

import asyncio
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import router
from app.config import Settings
from app.db import Database
from app.embeddings import Embeddings
from app.errors import AppError
from app.ingestion import Worker
from app.logging_config import configure
from app.mcp import create_mcp
from app.models import AgentRunner

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings, encoder=None, agent=None):
        self.settings = settings
        self.db = Database(settings.database_url)
        self.encoder = encoder or Embeddings(settings)
        self.agent = agent or AgentRunner(settings)
        self.worker = None
        self.ready = False
        self.error = None
        self.stop = threading.Event()

    def initialize(self):
        try:
            configure()
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.db.migrate()
            self.encoder.load()
            if self.stop.is_set():
                return
            self.worker = Worker(self.db, self.settings, self.encoder, self.agent)
            self.worker.start()
            self.ready = True
        except Exception as exc:
            self.error = exc.message if isinstance(exc, AppError) else "初始化失败，请检查数据库、模型及工作者锁"
            log.error("initialization_failed type=%s", type(exc).__name__)


def create_app(settings=None, *, encoder=None, agent=None, initialize=True):
    runtime = Runtime(settings or Settings(), encoder, agent)
    mcp = create_mcp(runtime)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(asyncio.to_thread(runtime.initialize)) if initialize else None
        if not initialize:
            runtime.ready = True  # Only used with explicitly injected test/runtime dependencies.
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                runtime.ready = False
                runtime.stop.set()
                runtime.agent.stop.set()
                if runtime.worker:
                    await asyncio.to_thread(runtime.worker.close)
                if task:
                    await task

    app = FastAPI(title='RAG Knowledge Base', version='0.1.0', lifespan=lifespan)
    app.state.runtime = runtime
    app.state.mcp = mcp

    @app.middleware('http')
    async def request_id(request: Request, call_next):
        started = time.monotonic()
        candidate = request.headers.get('X-Request-ID', '')
        request.state.request_id = candidate if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', candidate) else str(uuid4())
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        log.info('http_request status=%s', response.status_code, extra={
            'request_id': request.state.request_id, 'elapsed_ms': round((time.monotonic()-started)*1000, 2)})
        return response

    @app.exception_handler(AppError)
    async def application_error(request, exc):
        return JSONResponse(exc.payload(request.state.request_id), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(AppError('INVALID_ARGUMENT', '请求参数不符合接口约定', 422).payload(
            request.state.request_id), status_code=422)

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        log.error('database_error type=%s', type(exc).__name__)
        return JSONResponse(AppError('DATABASE_UNAVAILABLE', '数据库暂不可用', 503, True).payload(
            request.state.request_id), status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.error('unexpected_error type=%s', type(exc).__name__, extra={
            'request_id': request.state.request_id, 'error_code': 'INTERNAL_ERROR'})
        return JSONResponse(AppError('INTERNAL_ERROR', '服务内部错误，请提供请求编号排查', 500).payload(
            request.state.request_id), status_code=500)

    @app.get('/health')
    def health():
        return {'status': 'alive'}

    @app.get('/ready')
    def ready():
        if runtime.worker and runtime.worker.stop.is_set():
            runtime.ready = False
            runtime.error = '工作者已停止，请重启后端恢复任务'
        if runtime.ready:
            try:
                with runtime.db.connect() as conn:
                    conn.execute('SELECT 1')
            except psycopg.Error:
                return JSONResponse({'ready': False, 'error': 'DATABASE_UNAVAILABLE'}, status_code=503)
        return JSONResponse({'ready': runtime.ready, 'error': runtime.error},
                            status_code=200 if runtime.ready else 503)

    app.include_router(router(runtime))
    app.mount('/mcp', mcp.streamable_http_app())
    return app


app = create_app()

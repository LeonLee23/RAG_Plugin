import asyncio
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import psycopg
import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyHttpUrl

from app import knowledge
from app.chunking import build_chunks
from app.embeddings import Embeddings
from app.errors import AppError
from app.ingestion import publish_document, validate_relations
from app.main import create_app
from app.parsers import Block
from app.retrieval import vector
from app.retrieval.search import search
from app.schemas import GraphResult, KnowledgeBaseCreate, SearchRequest
from tests.conftest import FakeAgent

pytestmark = pytest.mark.integration


def prepare(db, settings, encoder, kb_id, text, triple=None):
    uploaded = knowledge.upload_document(db, settings, kb_id, 'fixture.pdf', b'%PDF-1.7\n'+uuid4().hex.encode())
    doc_id = uploaded['document']['id']
    with db.connect() as conn:
        row = conn.execute("UPDATE documents SET status='processing' WHERE id=%s RETURNING *", (doc_id,)).fetchone()
    chunks = build_chunks([Block('b1', 'text', text, {'kind': 'pdf', 'page': 1})], encoder)
    for c, v in zip(chunks, encoder.encode([c['retrieval_text'] for c in chunks])):
        c['embedding'] = v
    edges = []
    if triple:
        s, p, t = triple
        result = GraphResult.model_validate({'relations': [{'chunk_id': str(chunks[0]['id']),
            'subject': {'name': s, 'type': 'person' if s == '张三' else 'project'}, 'predicate': p,
            'object': {'name': t, 'type': 'project' if t == '凤凰项目' else 'product'}, 'quote': text,
            'occurrence': 0}]})
        edges = validate_relations(result, chunks)
    return row, chunks, edges


def publish(db, settings, encoder, kb_id, text, triple=None):
    row, chunks, edges = prepare(db, settings, encoder, kb_id, text, triple)
    publish_document(db, settings, row, chunks, edges, {})
    return row, chunks


@pytest.fixture
def indexed(db, settings, encoder, kb):
    first = publish(db, settings, encoder, kb['id'], '张三负责凤凰项目。', ('张三', '负责', '凤凰项目'))
    second = publish(db, settings, encoder, kb['id'], '凤凰项目使用星河数据库。', ('凤凰项目', '使用', '星河数据库'))
    return first, second


def test_four_modes_and_graph_support(db, settings, encoder, kb, indexed):
    for mode in ('vector', 'keyword', 'graph', 'hybrid'):
        result = search(db, settings, encoder, kb['id'], SearchRequest(
            query='张三负责的项目使用什么数据库？', mode=mode, top_k=1))
        assert result['results'] and result['kb_id'] == str(kb['id'])
        assert len(result['results']) == 1
        if mode == 'graph':
            assert result['supporting_results']
            assert len(result['graph_paths'][0]['edges']) == 2
            assert {e['evidence_id'] for e in result['graph_paths'][0]['edges']} == {'E1', 'E2'}


def test_remote_vectors_and_legacy_profile(db, settings, kb, monkeypatch):
    settings = settings.model_copy(update={"embedding_base_url": AnyHttpUrl("http://embedding.example.com/v1")})
    encoder = Embeddings(settings)
    encoder.load()
    calls = []

    def respond(request):
        calls.append(json.loads(request.content)["input"][0])
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1] * 1792}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(httpx, "post", client.post)
        publish(db, settings, encoder, kb['id'], '张三负责凤凰项目。')
        query = SearchRequest(query='凤凰项目', mode='vector')
        result = search(db, settings, encoder, kb['id'], query)
        assert result['results'][0]['text'] == '张三负责凤凰项目。'
        assert result['results'][0]['scores']['vector_similarity'] == pytest.approx(1)

        with db.connect() as conn:
            conn.execute("""UPDATE knowledge_bases SET embedding_profile = %s::jsonb WHERE id=%s""", (
                json.dumps({"model": "BAAI/bge-small-zh-v1.5", "revision": "7999e1d3359715c523056ef9478215996d62a620",
                            "dimension": 512, "normalize": True,
                            "query_instruction": "为这个句子生成表示以用于检索相关文章："}), kb['id']))
        with pytest.raises(AppError) as error:
            search(db, settings, encoder, kb['id'], query)
        assert error.value.code == 'EMBEDDING_PROFILE_MISMATCH'
        keyword = SearchRequest(query='凤凰项目', mode='keyword')
        assert search(db, settings, encoder, kb['id'], keyword)['results']
    assert len(calls) == 2


def test_isolation_and_cascade(db, settings, encoder, kb, indexed):
    other = knowledge.create_kb(db, settings, KnowledgeBaseCreate(name='B'))
    try:
        bdoc, _ = publish(db, settings, encoder, other['id'], '凤凰项目使用不应泄漏的数据库。')
        for mode in ('vector', 'keyword', 'graph', 'hybrid'):
            result = search(db, settings, encoder, kb['id'], SearchRequest(query='凤凰项目', mode=mode))
            assert all(r['document_id'] != str(bdoc['id']) for r in result['results']+result['supporting_results'])
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db.connect() as conn:
                conn.execute('UPDATE chunks SET kb_id=%s WHERE id=%s', (other['id'], indexed[0][1][0]['id']))
        deleted = indexed[1][0]['id']
        knowledge.delete_document(db, settings, kb['id'], deleted)
        for mode in ('vector', 'keyword', 'graph', 'hybrid'):
            result = search(db, settings, encoder, kb['id'], SearchRequest(query='凤凰项目', mode=mode))
            assert all(r['document_id'] != str(deleted) for r in result['results']+result['supporting_results'])
        with db.connect() as conn:
            assert not conn.execute('SELECT id FROM chunks WHERE document_id=%s', (deleted,)).fetchone()
    finally:
        knowledge.delete_kb(db, settings, other['id'])


def test_concurrent_duplicate_upload(db, settings, kb):
    content = b'%PDF-1.7\n identical bytes'
    def upload():
        return knowledge.upload_document(db, settings, kb['id'], 'a.pdf', content)
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(lambda _: upload(), range(2)))
    assert a['document']['id'] == b['document']['id']
    assert sorted([a['deduplicated'], b['deduplicated']]) == [False, True]
    assert knowledge.list_documents(db, kb['id'])['total'] == 1


def test_delete_before_publish_and_stale_attempt(db, settings, encoder, kb):
    row, chunks, edges = prepare(db, settings, encoder, kb['id'], '删除竞争测试。')
    release = threading.Event()
    def delayed_publish():
        assert release.wait(5)
        with pytest.raises(AppError):
            publish_document(db, settings, row, chunks, edges, {})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(delayed_publish)
        knowledge.delete_document(db, settings, kb['id'], row['id'])
        release.set()
        future.result()
    row, chunks, edges = prepare(db, settings, encoder, kb['id'], '旧任务不能发布。')
    with db.connect() as conn:
        conn.execute('UPDATE documents SET attempt=attempt+1 WHERE id=%s', (row['id'],))
    with pytest.raises(AppError, match='失效'):
        publish_document(db, settings, row, chunks, edges, {})
    with db.connect() as conn:
        assert not conn.execute('SELECT id FROM chunks WHERE kb_id=%s', (kb['id'],)).fetchone()


def test_publish_rolls_back_all_chunks(db, settings, encoder, kb):
    row, chunks, edges = prepare(db, settings, encoder, kb['id'], '原子发布。')
    chunks.append({**chunks[0], 'id': uuid4()})  # duplicate ordinal fails after first INSERT
    with pytest.raises(psycopg.errors.UniqueViolation):
        publish_document(db, settings, row, chunks, edges, {})
    with db.connect() as conn:
        assert not conn.execute('SELECT id FROM chunks WHERE document_id=%s', (row['id'],)).fetchone()
        assert conn.execute('SELECT status FROM documents WHERE id=%s', (row['id'],)).fetchone()['status'] == 'processing'


def test_partial_failure_uses_savepoint(db, settings, encoder, kb, indexed, monkeypatch):
    def fail(conn, *args):
        conn.execute('SELECT 1/0')
    monkeypatch.setattr(vector, 'recall', fail)
    with pytest.raises(AppError):
        search(db, settings, encoder, kb['id'], SearchRequest(query='凤凰项目'))
    result = search(db, settings, encoder, kb['id'], SearchRequest(query='凤凰项目', allow_partial=True))
    assert result['degraded'] and result['failed_modes'] == ['vector']
    assert result['results']


def test_http_contract_and_download_ownership(db, settings, encoder, kb, indexed):
    app = create_app(settings, encoder=encoder, agent=FakeAgent(), initialize=False)
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/ready').status_code == 200
        prefix = f"/api/knowledge-bases/{kb['id']}"
        assert client.post(prefix+'/search', json={'query': ' ', 'extra': 1}).status_code == 422
        result = client.post(prefix+'/search', json={'query': '凤凰项目', 'mode': 'graph'})
        assert result.status_code == 200, result.text
        assert result.headers['X-Request-ID']
        doc_id = indexed[0][0]['id']
        assert client.get(prefix+f'/documents/{doc_id}/file').status_code == 200
        assert client.get(f'/api/knowledge-bases/{uuid4()}/documents/{doc_id}/file').status_code == 404
        assert client.get(prefix+f'/documents/{doc_id}/assets/no-image').status_code == 404
        assert client.post(prefix+f'/documents/{doc_id}/retry').status_code == 409


def test_real_mcp_protocol(db, settings, encoder, kb, indexed):
    app = create_app(settings, encoder=encoder, agent=FakeAgent(), initialize=False)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        async def run():
            async with httpx.AsyncClient(trust_env=False) as client, streamable_http_client(
                f'http://127.0.0.1:{port}/mcp/', http_client=client
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {t.name for t in tools.tools} == {'list_knowledge_bases', 'search_knowledge_base'}
                    result = await session.call_tool('search_knowledge_base', {
                        'kb_id': str(kb['id']), 'query': '凤凰项目', 'mode': 'graph'})
                    assert not result.isError and result.structuredContent['results']
                    error = await session.call_tool('search_knowledge_base', {
                        'kb_id': str(uuid4()), 'query': '凤凰项目'})
                    assert error.isError
        asyncio.run(run())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()

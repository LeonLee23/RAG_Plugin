import asyncio
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app import ingestion, knowledge
from app.config import Settings
from app.storage import lock
from scripts.seed_demo import fixtures
from tests.test_core import error

pytestmark = pytest.mark.integration


def writer(folder, kb, number, queue):
    root = Path(folder)
    settings = Settings(root / 'registry', (root,))
    source = root / f'{number}.md'
    source.write_text(f'# 并发\n进程 {number}', encoding='utf-8')
    try:
        task = ingestion.import_document(settings, kb, str(source), f'op-{number}')
        ingestion.run_task(settings, kb, task['task_id'])
        result = ingestion.publish_document(settings, kb, task['task_id'], 1)
        queue.put(result['doc_id'])
    except Exception as exc:
        queue.put(f'error:{type(exc).__name__}:{exc}')


def test_two_real_writers(settings, kb, tmp_path):
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    processes = [ctx.Process(target=writer, args=(str(tmp_path), kb, i, queue)) for i in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        if process.is_alive():
            process.terminate()
            process.join()
            pytest.fail('Writer exceeded deadline')
        assert process.exitcode == 0
    results = [queue.get(timeout=5) for _ in processes]
    assert all(not result.startswith('error:') for result in results), results
    assert len(knowledge.list_documents(settings, kb)['items']) == 2


def test_lock_released_after_killed_process(tmp_path):
    target = tmp_path / '.lock'
    ready = tmp_path / 'ready'
    script = ('from pathlib import Path; import time; from app.storage import lock; '
              f'p=Path({str(target)!r}); r=Path({str(ready)!r}); '
              'ctx=lock(p); ctx.__enter__(); r.write_text("ready"); time.sleep(60)')
    process = subprocess.Popen([sys.executable, '-c', script], creationflags=(subprocess.CREATE_NO_WINDOW
                               if os.name == 'nt' else 0))
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        with error('KB_BUSY'), lock(target, timeout=0):
            pass
    finally:
        process.kill()
        process.wait(timeout=10)
    with lock(target, timeout=1):
        assert target.exists()


def test_real_stdio_lifecycle(tmp_path):
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'app.main', '--state-dir',
            str(tmp_path / 'state'), '--allow-root', str(tmp_path)], cwd=str(Path(__file__).resolve().parents[1]))
        async with stdio_client(params) as (reader, writer_stream), ClientSession(reader, writer_stream) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            assert len(tools) == 17
            assert sum(t.annotations.readOnlyHint for t in tools) == 9
            assert next(t for t in tools if t.name == 'delete_document').annotations.destructiveHint

            async def call(name, args, code=None):
                result = await client.call_tool(name, args)
                data = json.loads(result.content[0].text)
                if code:
                    assert result.isError and data['error']['code'] == code
                else:
                    assert not result.isError, data
                    assert data['request_id']
                return data

            created = await call('create_knowledge_base', {'path': str(tmp_path / 'package'), 'name': 'MCP',
                                                          'operation_id': 'create'})
            kb = created['kb_id']
            await call('list_knowledge_bases', {})
            source = tmp_path / 'source.md'
            source.write_text('# stdio\n真实协议 PHX-503', encoding='utf-8')
            task = await call('import_document', {'kb_id': kb, 'source_path': str(source), 'operation_id': 'import'})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                task = await call('get_ingestion_task', {'kb_id': kb, 'task_id': task['task_id']})
                if task['state'] != 'queued' and task['state'] != 'parsing':
                    break
                await asyncio.sleep(0.1)
            assert task['state'] == 'ready_to_publish'
            published = await call('publish_document', {'kb_id': kb, 'task_id': task['task_id'], 'attempt': 1})
            hits = await call('search_knowledge_base', {'kb_id': kb, 'query': 'PHX-503', 'match': 'exact'})
            ev = await call('read_evidence', {'kb_id': kb, 'doc_id': published['doc_id'],
                'doc_revision': published['doc_revision'], 'node_id': hits['items'][0]['node_id'],
                'expected_kb_revision': hits['kb_revision']})
            await call('validate_evidence', {'kb_id': kb, 'expected_kb_revision': ev['kb_revision'],
                                            'evidence_refs': [ev['evidence_id']]})
            await call('search_knowledge_base', {'kb_id': '0' * 32, 'query': 'x'}, 'KB_NOT_FOUND')
            await call('search_knowledge_base', {'kb_id': kb, 'query': 'x', 'limit': 101}, 'INVALID_ARGUMENT')
            pdf = fixtures(tmp_path / 'pictures')[0]
            task = await call('import_document', {'kb_id': kb, 'source_path': str(pdf), 'operation_id': 'image'})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                task = await call('get_ingestion_task', {'kb_id': kb, 'task_id': task['task_id']})
                if task['state'] not in ('queued', 'parsing'):
                    break
                await asyncio.sleep(0.1)
            assert task['state'] == 'awaiting_enrichment'
            picture = await client.call_tool('read_ingestion_input', {'kb_id': kb, 'task_id': task['task_id'],
                'attempt': 1, 'item_id': task['pending_images'][0]})
            assert not picture.isError
            assert picture.content[1].type == 'image' and picture.content[1].mimeType == 'image/png'
            data = json.loads(picture.content[0].text)
            await call('submit_enrichment', {'kb_id': kb, 'task_id': task['task_id'], 'attempt': 1,
                'items': [{'item_id': data['item_id'], 'kind': 'image', 'source_sha256': data['source_sha256'],
                           'text': 'Explicit protocol fixture, not a model claim: RAG-42'}]})
            doc = await call('publish_document', {'kb_id': kb, 'task_id': task['task_id'], 'attempt': 1})
            asset = await client.call_tool('read_asset', {'kb_id': kb, 'doc_id': doc['doc_id'],
                'doc_revision': doc['doc_revision'], 'asset_id': data['item_id']})
            assert not asset.isError and asset.content[1].type == 'image'
    asyncio.run(scenario())

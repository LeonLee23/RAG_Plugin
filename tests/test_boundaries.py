import shutil

import pytest

from app import ingestion, knowledge, storage
from app.config import Settings
from app.retrieval import search
from app.schemas import Manifest
from app.storage import atomic_json, inside, manifest, mounted
from tests.conftest import ingest, publish
from tests.test_core import error


def test_path_isolation_and_package_schema(settings, kb, tmp_path):
    for name in ('../escape', '/absolute', 'C:/escape', 'a\\b', 'assets/../../x', 'a:stream'):
        with error('PATH_OUT_OF_SCOPE'):
            inside(tmp_path, name)
    with error('PATH_OUT_OF_SCOPE'):
        knowledge.create_knowledge_base(settings, str(tmp_path.parent / 'outside'), 'bad', '', 'bad')
    source = tmp_path / 'source.md'
    source.write_text('# 标题\n正文', encoding='utf-8')
    task = ingest(settings, kb, source)
    kb2 = knowledge.create_knowledge_base(settings, str(tmp_path / 'kb2'), '二号库', '', 'create')['kb_id']
    with error('TASK_NOT_FOUND'):
        ingestion.get_ingestion_task(settings, kb2, task['task_id'])
    doc = publish(settings, kb, task)
    with error('DOCUMENT_NOT_FOUND'):
        search.read_evidence(settings, kb2, doc['doc_id'], doc['doc_revision'], 'n1')
    root, value = mounted(settings, kb)
    value['format_version'] = 2
    with pytest.raises(ValueError):
        atomic_json(root / 'manifest.json', value, Manifest)


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def test_read_only_move_and_duplicate_mount(settings, kb, tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('# 标题\n可搬迁正文', encoding='utf-8')
    published = publish(settings, kb, ingest(settings, kb, source))
    root, _ = mounted(settings, kb)
    copy = tmp_path / 'copy'
    shutil.copytree(root, copy)
    with error('KB_ID_CONFLICT'):
        knowledge.mount_knowledge_base(settings, str(copy))
    knowledge.unmount_knowledge_base(settings, kb)
    settings2 = Settings(tmp_path / 'other-registry', (tmp_path,))
    before = snapshot(copy)
    knowledge.mount_knowledge_base(settings2, str(copy))
    hit = search.search_knowledge_base(settings2, kb, '可搬迁', match='exact')['items'][0]
    search.read_evidence(settings2, kb, hit['doc_id'], published['doc_revision'], hit['node_id'])
    with error('KB_READ_ONLY'):
        ingestion.import_document(settings2, kb, str(tmp_path / 'missing.md'), 'write')
    knowledge.unmount_knowledge_base(settings2, kb)
    assert snapshot(copy) == before
    knowledge.mount_knowledge_base(settings2, str(copy))
    shutil.move(str(copy), str(tmp_path / 'moved'))
    assert knowledge.list_knowledge_bases(settings2)['items'][0]['error']['code'] == 'KB_OFFLINE'
    knowledge.mount_knowledge_base(settings2, str(tmp_path / 'moved'), replace=True)
    assert knowledge.list_knowledge_bases(settings2)['items'][0]['available']


def test_atomic_replace_failure_and_stale_cursor(settings, kb, tmp_path, monkeypatch):
    for i in range(2):
        source = tmp_path / f'{i}.md'
        source.write_text(f'# 标题\n正文 {i}', encoding='utf-8')
        publish(settings, kb, ingest(settings, kb, source, f'import-{i}'))
    first = knowledge.list_documents(settings, kb, limit=1)
    root, value = mounted(settings, kb)
    old = (root / 'manifest.json').read_bytes()
    real_replace = storage.os.replace

    def fail(source, target):
        if target.name == 'manifest.json':
            raise PermissionError('simulated Windows sharing violation')
        return real_replace(source, target)

    monkeypatch.setattr(storage.os, 'replace', fail)
    doc = first['items'][0]
    with error('IO_ERROR'):
        knowledge.delete_document(settings, kb, doc['doc_id'], doc['doc_revision'], 'delete')
    assert (root / 'manifest.json').read_bytes() == old
    monkeypatch.setattr(storage.os, 'replace', real_replace)
    knowledge.delete_document(settings, kb, doc['doc_id'], doc['doc_revision'], 'delete')
    with error('STALE_REVISION'):
        knowledge.list_documents(settings, kb, cursor=first['next_cursor'])
    assert manifest(root)['kb_revision'] > value['kb_revision']


def test_active_tasks_block_unmount_and_stale_write(settings, kb, tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('正文', encoding='utf-8')
    task = ingest(settings, kb, source)
    with error('KB_BUSY'):
        knowledge.unmount_knowledge_base(settings, kb)
    with error('KB_BUSY'):
        knowledge.mount_knowledge_base(settings, str(tmp_path / 'kb'), 'read_only')
    with error('INVALID_ARGUMENT'):
        ingestion.read_ingestion_input(settings, kb, task['task_id'], 1, '../source.md')
    ingestion.cancel_ingestion(settings, kb, task['task_id'], 1)
    knowledge.unmount_knowledge_base(settings, kb)


def test_reparse_escape_when_supported(settings, kb, tmp_path):
    link = tmp_path / 'link'
    try:
        link.symlink_to(tmp_path / 'kb', target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit symlink creation')
    with error('PATH_OUT_OF_SCOPE'):
        knowledge.mount_knowledge_base(settings, str(link))


def test_raw_json_malformed_is_error(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_text('{broken', encoding='utf-8')
    with error('FORMAT_UNSUPPORTED'):
        storage.read_json(path, Manifest)


def test_tampered_artifact_and_reference(settings, kb, tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('原文证据', encoding='utf-8')
    doc = publish(settings, kb, ingest(settings, kb, source))
    ev = search.read_evidence(settings, kb, doc['doc_id'], doc['doc_revision'], 'n0')
    with error('INVALID_ARGUMENT'):
        search.validate_evidence(settings, kb, ev['kb_revision'], [ev['evidence_id'][:-8] + 'abcdefgh'])
    root, value = mounted(settings, kb)
    artifact = inside(root, value['documents'][doc['doc_id']]['path'] + '/tree.json')
    artifact.write_text('{}', encoding='utf-8')
    with error('INDEX_STALE'):
        search.validate_evidence(settings, kb, ev['kb_revision'], [ev['evidence_id']])

def test_junction_escape_on_windows(settings, kb, tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows junction test')
    link = tmp_path / 'junction'
    target = tmp_path / 'kb'
    command = f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True)
    assert result.returncode == 0
    with error('PATH_OUT_OF_SCOPE'):
        knowledge.mount_knowledge_base(settings, str(link))


def test_task_candidate_cannot_point_to_another_task(settings, kb, tmp_path):
    from app.schemas import Task
    source = tmp_path / 'source.md'
    source.write_text('归属测试', encoding='utf-8')
    task = ingest(settings, kb, source)
    root, _ = mounted(settings, kb)
    path = ingestion.task_path(root, task['task_id'])
    value = storage.read_json(path, Task)
    value['candidate']['path'] = '.work/' + '0' * 32 + '/attempt-1'
    storage.atomic_json(path, value, Task)
    with error('FORMAT_UNSUPPORTED'):
        ingestion.read_ingestion_input(settings, kb, task['task_id'], 1, 'n0')

def test_readonly_pending_package_can_promote_to_resume(settings, kb, tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('等待恢复', encoding='utf-8')
    task = ingestion.import_document(settings, kb, str(source), 'one')
    other = Settings(tmp_path / 'second-state', (tmp_path,))
    knowledge.mount_knowledge_base(other, str(tmp_path / 'kb'))
    knowledge.mount_knowledge_base(other, str(tmp_path / 'kb'), 'read_write')
    ingestion.cancel_ingestion(other, kb, task['task_id'], 1)
    knowledge.unmount_knowledge_base(other, kb)

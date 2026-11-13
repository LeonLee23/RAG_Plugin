import json

import pytest

from app import ingestion, knowledge
from app.errors import AppError
from app.retrieval import search
from app.storage import inside, mounted
from scripts.seed_demo import fixtures
from tests.conftest import ingest, publish


def error(code):
    return pytest.raises(AppError, match='.*', check=lambda e: e.code == code)


def test_markdown_tree_paging_and_reference(settings, kb, tmp_path):
    source = tmp_path / 'guide.md'
    source.write_text('前言\n# 同名\n中文数据库星河\n```\n# 假标题\n```\n### 子节\n' + '长正文\n' * 4000
                      + '# 同名\n末尾 PHX-503\n', encoding='utf-8')
    task = ingest(settings, kb, source)
    assert task['state'] == 'ready_to_publish'
    assert not knowledge.list_documents(settings, kb)['items']
    result = publish(settings, kb, task)
    hit = search.search_knowledge_base(settings, kb, 'PHX-503', match='exact')['items'][0]
    outline = search.get_document_outline(settings, kb, hit['doc_id'], 'n0')['items']
    assert [n['title'] for n in outline] == ['同名', '同名']
    children = search.get_document_outline(settings, kb, hit['doc_id'], outline[0]['node_id'])['items']
    assert [n['title'] for n in children] == ['子节']
    evidence = search.read_evidence(settings, kb, hit['doc_id'], hit['doc_revision'], children[0]['node_id'])
    assert evidence['truncated'] and len(evidence['text']) <= 12000
    second = search.read_evidence(settings, kb, hit['doc_id'], hit['doc_revision'], children[0]['node_id'],
                                  cursor=evidence['next_cursor'])
    assert evidence['end'] == second['start']
    assert second['locations'][0]['location']['line_start'] > evidence['locations'][0]['location']['line_start']
    assert search.validate_evidence(settings, kb, result['kb_revision'], [evidence['evidence_id']])['valid']
    assert search.search_knowledge_base(settings, kb, '数据库')['items']
    assert not search.search_knowledge_base(settings, kb, '不存在的词zzzz', match='exact')['items']


def test_updates_idempotency_cancel_and_delete(settings, kb, tmp_path):
    source = tmp_path / 'guide.md'
    source.write_text('# 标题\n旧内容', encoding='utf-8')
    task = ingest(settings, kb, source)
    first = publish(settings, kb, task)
    assert publish(settings, kb, task) == first
    assert ingestion.import_document(settings, kb, str(source), 'import')['doc_id'] == first['doc_id']
    assert ingestion.import_document(settings, kb, str(source), 'duplicate')['duplicate']
    source.write_text('# 标题\n新内容', encoding='utf-8')
    with error('OPERATION_CONFLICT'):
        ingestion.import_document(settings, kb, str(source), 'import')
    update = ingest(settings, kb, source, 'update', doc_id=first['doc_id'], expected_doc_revision=first['doc_revision'])
    assert search.search_knowledge_base(settings, kb, '旧内容', match='exact')['warnings'][0]['code'] == 'UPDATE_PENDING'
    second = publish(settings, kb, update)
    with error('STALE_REVISION'):
        search.read_evidence(settings, kb, first['doc_id'], first['doc_revision'], 'n1')
    update2 = ingest(settings, kb, source, 'update2', doc_id=first['doc_id'], expected_doc_revision=second['doc_revision'])
    deleted = knowledge.delete_document(settings, kb, first['doc_id'], second['doc_revision'], 'delete')
    assert knowledge.delete_document(settings, kb, first['doc_id'], second['doc_revision'], 'delete') == deleted
    with error('ATTEMPT_STALE'):
        publish(settings, kb, update2)
    assert not search.search_knowledge_base(settings, kb, '新内容')['items']
    assert ingestion.get_ingestion_task(settings, kb, update2['task_id'])['state'] == 'cancelled'


def test_recovery_and_old_attempt(settings, kb, tmp_path):
    source = tmp_path / 'guide.md'
    source.write_text('# 标题\n正文', encoding='utf-8')
    task = ingestion.import_document(settings, kb, str(source), 'import')
    resumed = ingestion.import_document(settings, kb, str(source), 'import', resume=True)
    assert resumed['attempt'] == 2
    with error('ATTEMPT_STALE'):
        ingestion.cancel_ingestion(settings, kb, task['task_id'], 1)
    ingestion.run_task(settings, kb, task['task_id'])
    ready = ingestion.get_ingestion_task(settings, kb, task['task_id'])
    ingestion.cancel_ingestion(settings, kb, task['task_id'], ready['attempt'])
    with error('ATTEMPT_STALE'):
        ingestion.import_document(settings, kb, str(source), 'import', resume=True)
    with error('ATTEMPT_STALE'):
        publish(settings, kb, ready)


@pytest.mark.parametrize('format_index', [0, 1])
def test_pdf_docx_images_and_locations(settings, kb, tmp_path, format_index):
    source = fixtures(tmp_path / 'demo')[format_index]
    task = ingest(settings, kb, source)
    assert task['state'] == 'awaiting_enrichment'
    with error('ENRICHMENT_REQUIRED'):
        publish(settings, kb, task)
    items = []
    for asset_id in task['pending_images']:
        inp = ingestion.read_ingestion_input(settings, kb, task['task_id'], task['attempt'], asset_id)
        assert inp['_image']['data'] and inp['_image']['mimeType'] == 'image/png'
        assert inp['location']['kind'] == ('pdf' if format_index == 0 else 'docx')
        items.append({'item_id': asset_id, 'kind': 'image', 'source_sha256': inp['source_sha256'],
                      'text': '显式测试替身：Phoenix uses StarRiver DB', 'visible_text': ['RAG-42']})
    ready = ingestion.submit_enrichment(settings, kb, task['task_id'], task['attempt'], items)
    assert ready['state'] == 'ready_to_publish'
    published = publish(settings, kb, ready)
    hit = search.search_knowledge_base(settings, kb, 'RAG-42', match='exact')['items'][0]
    assert hit['evidence_type'] == 'image_description'
    asset = search.read_asset(settings, kb, hit['doc_id'], hit['doc_revision'], hit['asset_id'])
    assert search.validate_evidence(settings, kb, published['kb_revision'], [asset['evidence_id']])['valid']
    if format_index == 1:
        text_hit = search.search_knowledge_base(settings, kb, '02:00', match='exact')['items'][0]
        ev = search.read_evidence(settings, kb, text_hit['doc_id'], text_hit['doc_revision'], text_hit['node_id'])
        locs = [m['location'] for m in ev['locations']]
        assert any(loc['row'] == 1 and loc['cell'] == 2 for loc in locs)
        assert all(loc['page'] is None for loc in locs)


def test_markdown_images_failure_and_source_integrity(settings, kb, tmp_path):
    fixtures(tmp_path / 'demo')
    source = tmp_path / 'demo' / 'image.md'
    source.write_text('# 说明\n![图](project-diagram.png)\n', encoding='utf-8')
    task = ingest(settings, kb, source)
    inp = ingestion.read_ingestion_input(settings, kb, task['task_id'], 1, task['pending_images'][0])
    item = dict(item_id=inp['item_id'], kind='image', source_sha256=inp['source_sha256'], text='技术失败', status='failed')
    failed = ingestion.submit_enrichment(settings, kb, task['task_id'], 1, [item])
    assert failed['state'] == 'awaiting_enrichment'
    with error('ENRICHMENT_REQUIRED'):
        publish(settings, kb, failed)
    item.update(text='装饰图，没有可读文字', status='decorative')
    ready = ingestion.submit_enrichment(settings, kb, task['task_id'], 1, [item])
    result = publish(settings, kb, ready)
    assert result['coverage']['decorative'] == 1
    root, manifest = mounted(settings, kb)
    doc = manifest['documents'][task['doc_id']]
    inside(root, doc['path'] + '/source.md').write_text('tampered', encoding='utf-8')
    with error('INDEX_STALE'):
        search.search_knowledge_base(settings, kb, '说明')
    assert knowledge.list_documents(settings, kb)['items'][0]['index_valid'] is False


def test_failed_update_preserves_published_document(settings, kb, tmp_path):
    source = tmp_path / 'ok.md'
    source.write_text('原始正文', encoding='utf-8')
    first = publish(settings, kb, ingest(settings, kb, source))
    invalid = tmp_path / 'broken.pdf'
    invalid.write_bytes(b'%PDF-broken')
    task = ingest(settings, kb, invalid, 'bad', doc_id=first['doc_id'], expected_doc_revision=first['doc_revision'])
    assert task['state'] == 'failed'
    assert task['error']['code'] == 'PARSE_FAILED'
    assert search.search_knowledge_base(settings, kb, '原始正文', match='exact')['items']


def test_published_receipt_survives_old_task_file(settings, kb, tmp_path):
    source = tmp_path / 'receipt.md'
    source.write_text('回执正文', encoding='utf-8')
    task = ingest(settings, kb, source)
    result = publish(settings, kb, task)
    root, _ = mounted(settings, kb)
    raw_task = json.loads(ingestion.task_path(root, task['task_id']).read_text(encoding='utf-8'))
    assert raw_task['state'] == 'ready_to_publish'  # Receipt is the actual atomic commit.
    assert ingestion.get_ingestion_task(settings, kb, task['task_id'])['receipt'] == result

def test_resume_from_checkpoint_without_external_source(settings, kb, tmp_path):
    source = tmp_path / 'gone.md'
    source.write_text('断点源文件', encoding='utf-8')
    task = ingestion.import_document(settings, kb, str(source), 'import')
    source.unlink()
    resumed = ingestion.import_document(settings, kb, str(source), 'import', resume=True)
    assert resumed['task_id'] == task['task_id'] and resumed['attempt'] == 2
    ingestion.run_task(settings, kb, task['task_id'])
    ready = ingestion.get_ingestion_task(settings, kb, task['task_id'])
    assert publish(settings, kb, ready)['state'] == 'published'


def test_same_source_pending_imports_share_task(settings, kb, tmp_path):
    source = tmp_path / 'same.md'
    source.write_text('相同的待处理输入', encoding='utf-8')
    first = ingestion.import_document(settings, kb, str(source), 'one')
    second = ingestion.import_document(settings, kb, str(source), 'two')
    assert first['task_id'] == second['task_id']
    ingestion.run_task(settings, kb, first['task_id'])
    publish(settings, kb, ingestion.get_ingestion_task(settings, kb, first['task_id']))
    assert ingestion.import_document(settings, kb, str(source), 'two')['state'] == 'published'
    assert len(knowledge.list_documents(settings, kb)['items']) == 1


def test_markdown_relative_image_bytes_are_part_of_idempotency(settings, kb, tmp_path):
    from PIL import Image
    image = tmp_path / 'image.png'
    Image.new('RGB', (20, 20), 'red').save(image)
    source = tmp_path / 'image.md'
    source.write_text('# 图片\n![图片](image.png)', encoding='utf-8')
    first = ingestion.import_document(settings, kb, str(source), 'one')
    Image.new('RGB', (20, 20), 'blue').save(image)
    with error('OPERATION_CONFLICT'):
        ingestion.import_document(settings, kb, str(source), 'one')
    second = ingestion.import_document(settings, kb, str(source), 'two')
    assert second['task_id'] != first['task_id']


def test_publish_recovers_rename_before_manifest_commit(settings, kb, tmp_path, monkeypatch):
    source = tmp_path / 'crash.md'
    source.write_text('原子发布恢复', encoding='utf-8')
    task = ingest(settings, kb, source)
    commit = ingestion.commit

    def interrupt(root, value):
        raise AppError('IO_ERROR', 'simulated interruption before manifest swap')

    monkeypatch.setattr(ingestion, 'commit', interrupt)
    with error('IO_ERROR'):
        publish(settings, kb, task)
    assert knowledge.list_documents(settings, kb)['items'] == []
    monkeypatch.setattr(ingestion, 'commit', commit)
    assert publish(settings, kb, task)['state'] == 'published'
    assert len(knowledge.list_documents(settings, kb)['items']) == 1

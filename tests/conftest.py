import pytest

from app import ingestion, knowledge
from app.config import Settings


def pytest_addoption(parser):
    parser.addoption('--live-model', action='store_true', help='Explicitly enable real host probes')


def pytest_collection_modifyitems(config, items):
    if not config.getoption('--live-model'):
        for item in items:
            if 'live_model' in item.keywords:
                item.add_marker(pytest.mark.skip(reason='Requires explicit --live-model'))


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / 'registry', (tmp_path,))


@pytest.fixture
def kb(settings, tmp_path):
    return knowledge.create_knowledge_base(settings, str(tmp_path / 'kb'), '测试库', '', 'create')['kb_id']


def ingest(settings, kb, path, operation='import', **kwargs):
    task = ingestion.import_document(settings, kb, str(path), operation, **kwargs)
    if task['state'] == 'queued':
        ingestion.run_task(settings, kb, task['task_id'])
        task = ingestion.get_ingestion_task(settings, kb, task['task_id'])
    return task


def publish(settings, kb, task):
    return ingestion.publish_document(settings, kb, task['task_id'], task['attempt'], task['expected_doc_revision'])

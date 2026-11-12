import os
import threading
from uuid import uuid4

import numpy as np
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.config import Settings
from app.db import Database


class FakeEncoder:
    """Deterministic test fixture only; production never imports this module."""
    def load(self):
        pass

    def token_count(self, text):
        return len(text)+2

    def encode(self, texts, kind='document'):
        vectors = np.zeros((len(texts), 512), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors


class FakeAgent:
    def __init__(self):
        self.stop = threading.Event()
        self.last_error = None
        self.last_provider = None


@pytest.fixture
def encoder():
    return FakeEncoder()


@pytest.fixture
def db():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TEST_DATABASE_URL for real PostgreSQL integration tests')
    name = conninfo_to_dict(url).get('dbname', '')
    if not name.endswith('_test'):
        pytest.fail('Refusing to run integration tests outside a *_test database')
    database = Database(url)
    database.migrate()
    # Each test owns a unique KB; never truncate another process's tables.
    yield database


@pytest.fixture
def settings(db, tmp_path):
    return Settings(database_url=db.url, data_dir=tmp_path, _env_file=None)


@pytest.fixture
def kb(db, settings):
    from app.knowledge import create_kb, delete_kb
    from app.schemas import KnowledgeBaseCreate

    row = create_kb(db, settings, KnowledgeBaseCreate(name='test-'+str(uuid4())))
    yield row
    delete_kb(db, settings, row['id'])

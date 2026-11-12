from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.config import MIGRATION_LOCK


class Database:
    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def connect(self):
        with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as conn:
            register_vector(conn)
            conn.commit()  # Type discovery must not establish a search snapshot.
            yield conn

    def migrate(self):
        with psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
            applied = {r['version'] for r in conn.execute("SELECT version FROM schema_migrations")}
            scripts = sorted(Path(__file__).with_name("sql").glob("*.sql"))
            if applied - {p.name for p in scripts}:
                raise RuntimeError("Database schema is newer than this application")
            for path in scripts:
                if path.name not in applied:
                    conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                    conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.name,))


def get_kb(conn, kb_id, *, lock=False):
    from app.errors import AppError

    row = conn.execute("SELECT * FROM knowledge_bases WHERE id=%s" +
                       (" FOR UPDATE" if lock else ""), (kb_id,)).fetchone()
    if not row:
        raise AppError("KB_NOT_FOUND", "知识库不存在", 404)
    return row

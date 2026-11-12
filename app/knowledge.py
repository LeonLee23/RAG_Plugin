import hashlib
import io
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.config import MAX_UPLOAD, PIPELINE_VERSION
from app.db import get_kb
from app.embeddings import DIMENSION, INPUT_TEMPLATE, QUERY_INSTRUCTION
from app.errors import AppError


def profile(settings):
    return {"model": settings.embedding_model, "revision": settings.embedding_revision,
            "dimension": DIMENSION, "normalize": True, "query_instruction": QUERY_INSTRUCTION,
            "input_template": INPUT_TEMPLATE}


def create_kb(db, settings, request):
    with db.connect() as conn:
        return conn.execute("""INSERT INTO knowledge_bases(id,name,description,embedding_profile)
            VALUES (%s,%s,%s,%s) RETURNING *""",
            (uuid4(), request.name, request.description, Jsonb(profile(settings)))).fetchone()


def list_kbs(db, limit=20, offset=0):
    with db.connect() as conn:
        total = conn.execute("SELECT count(*) AS n FROM knowledge_bases").fetchone()['n']
        rows = conn.execute("""SELECT k.*, (SELECT count(*) FROM documents d
            WHERE d.kb_id=k.id AND d.status='ready') AS ready_document_count
            FROM knowledge_bases k ORDER BY k.created_at,k.id LIMIT %s OFFSET %s""",
            (limit, offset)).fetchall()
    return {"items": rows, "total": total}


def document(conn, kb_id, doc_id, lock=False):
    row = conn.execute("SELECT * FROM documents WHERE kb_id=%s AND id=%s" +
                       (" FOR UPDATE" if lock else ""), (kb_id, doc_id)).fetchone()
    if not row:
        raise AppError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
    return row


def public_document(row):
    return {k: v for k, v in row.items() if k not in {"source_path", "asset_manifest", "processing_meta"}}


def validate_upload(filename, content):
    if not content:
        raise AppError("INVALID_ARGUMENT", "文件不能为空", 422)
    if len(content) > MAX_UPLOAD:
        raise AppError("FILE_TOO_LARGE", "单文件不得超过 20 MiB", 413)
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" and content.startswith(b"%PDF-"):
        return "pdf"
    if suffix == ".docx" and content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 5000 or sum(e.file_size for e in entries) > 100 * 1024 * 1024:
                    raise AppError("FILE_TOO_LARGE", "DOCX 解压量超过限制", 413)
                if not {"word/document.xml", "[Content_Types].xml"}.issubset(archive.namelist()):
                    raise ValueError("Missing document parts")
            return "docx"
        except (zipfile.BadZipFile, ValueError) as exc:
            raise AppError("UNSUPPORTED_FILE_TYPE", "DOCX 内容无效", 415) from exc
    raise AppError("UNSUPPORTED_FILE_TYPE", "仅支持有效的 PDF / DOCX 文件", 415)


def upload_document(db, settings, kb_id, filename, content):
    media_type = validate_upload(filename, content)
    digest = hashlib.sha256(content).hexdigest()
    doc_id = uuid4()
    folder = settings.data_dir / "documents" / str(doc_id)
    committed = False
    try:
        folder.mkdir(parents=True)
        source = folder / f"source.{media_type}"
        source.write_bytes(content)
        with db.connect() as conn:
            get_kb(conn, kb_id, lock=True)
            old = conn.execute("SELECT * FROM documents WHERE kb_id=%s AND sha256=%s",
                               (kb_id, digest)).fetchone()
            if old:
                return {"document": public_document(old), "deduplicated": True}
            row = conn.execute("""INSERT INTO documents
                (id,kb_id,filename,media_type,sha256,size_bytes,source_path,pipeline_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (doc_id, kb_id, filename.replace('\\', '/').split('/')[-1][:255], media_type,
                 digest, len(content), source.relative_to(settings.data_dir).as_posix(), PIPELINE_VERSION)
                ).fetchone()
        committed = True
        return {"document": public_document(row), "deduplicated": False}
    finally:
        if not committed:
            cleanup_files(settings, [doc_id])


def list_documents(db, kb_id, limit=20, offset=0):
    with db.connect() as conn:
        get_kb(conn, kb_id)
        total = conn.execute("SELECT count(*) AS n FROM documents WHERE kb_id=%s", (kb_id,)).fetchone()['n']
        rows = conn.execute("SELECT * FROM documents WHERE kb_id=%s ORDER BY created_at,id LIMIT %s OFFSET %s",
                            (kb_id, limit, offset)).fetchall()
    return {"items": [public_document(r) for r in rows], "total": total}


def retry_document(db, kb_id, doc_id):
    with db.connect() as conn:
        get_kb(conn, kb_id, lock=True)
        row = document(conn, kb_id, doc_id, lock=True)
        if row['status'] != 'failed':
            raise AppError("DOCUMENT_NOT_RETRYABLE", "仅失败文档可重试", 409)
        row = conn.execute("""UPDATE documents SET status='queued',attempt=attempt+1,stage=NULL,
            progress=0,error_code=NULL,error_message=NULL,updated_at=now()
            WHERE id=%s RETURNING *""", (doc_id,)).fetchone()
    return public_document(row)


def cleanup_files(settings, doc_ids):
    import logging

    root = (settings.data_dir / "documents").resolve()
    for doc_id in doc_ids:
        folder = (root / str(doc_id)).resolve()
        if folder.parent != root:
            raise ValueError("Unsafe cleanup path")
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except OSError:
            logging.getLogger(__name__).warning("cleanup_pending document_id=%s", doc_id)


def delete_document(db, settings, kb_id, doc_id):
    with db.connect() as conn:
        if not conn.execute("SELECT id FROM knowledge_bases WHERE id=%s FOR UPDATE", (kb_id,)).fetchone():
            return
        deleted = conn.execute("DELETE FROM documents WHERE kb_id=%s AND id=%s RETURNING id",
                               (kb_id, doc_id)).fetchone()
        if not deleted:
            return
        conn.execute("""DELETE FROM entities e WHERE kb_id=%s AND NOT EXISTS
            (SELECT 1 FROM relations r WHERE r.kb_id=e.kb_id
             AND (r.source_entity_id=e.id OR r.target_entity_id=e.id))""", (kb_id,))
        conn.execute("UPDATE knowledge_bases SET revision=revision+1 WHERE id=%s", (kb_id,))
    cleanup_files(settings, [doc_id])


def delete_kb(db, settings, kb_id):
    with db.connect() as conn:
        if not conn.execute("SELECT id FROM knowledge_bases WHERE id=%s FOR UPDATE", (kb_id,)).fetchone():
            return
        ids = [r['id'] for r in conn.execute("SELECT id FROM documents WHERE kb_id=%s", (kb_id,))]
        conn.execute("DELETE FROM knowledge_bases WHERE id=%s", (kb_id,))
    cleanup_files(settings, ids)


def file_path(db, settings, kb_id, doc_id, asset_id=None):
    with db.connect() as conn:
        row = document(conn, kb_id, doc_id)
        relative = row['source_path']
        if asset_id:
            asset = row['asset_manifest'].get(asset_id)
            if not asset or row['status'] != 'ready':
                raise AppError("ASSET_NOT_FOUND", "图片不存在", 404)
            relative = asset['path']
    path = (settings.data_dir / relative).resolve()
    expected = (settings.data_dir / "documents" / str(doc_id)).resolve()
    if not path.is_relative_to(expected) or not path.is_file():
        raise AppError("ASSET_NOT_FOUND", "文件不存在", 404)
    return path, row['filename']

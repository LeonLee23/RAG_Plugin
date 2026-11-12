import json
import logging
import shutil
import threading
import time
from dataclasses import asdict
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from app.chunking import build_chunks
from app.config import MAX_KB_CHUNKS, WORKER_LOCK
from app.db import get_kb
from app.errors import AppError
from app.knowledge import cleanup_files, document, profile
from app.parsers import parse_document
from app.schemas import GraphResult
from app.text import normalize, token_text

log = logging.getLogger(__name__)


def validate_relations(result, chunks):
    by_id = {c['id']: c for c in chunks}
    edges = []
    seen = set()
    for index, r in enumerate(result.relations):
        chunk = by_id.get(r.chunk_id)
        if not chunk:
            raise ValueError(f"relations[{index}].chunk_id: Evidence chunk not in input")
        start = -1
        for _ in range(r.occurrence + 1):
            start = chunk['evidence_text'].find(r.quote, start+1)
            if start < 0:
                raise ValueError(f"relations[{index}].quote: Quote is not in source, or occurrence is too large. "
                                 "Copy a contiguous substring including original whitespace and line breaks.")
        source = (r.subject.type, normalize(r.subject.name))
        target = (r.object.type, normalize(r.object.name))
        predicate = r.predicate.strip()
        if source == target or not source[1] or not target[1] or not predicate:
            raise ValueError(f"relations[{index}]: Invalid entity or self relation")
        end = start + len(r.quote)
        source_ranges = tuple((s['block_id'], s['source_start']+max(start-s['start'], 0),
            s['source_start']+min(end-s['start'], s['end']-s['start']))
            for s in chunk['source_spans'] if s['start'] < end and s['end'] > start)
        key = (source, predicate, target, source_ranges)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"source": source, "source_name": r.subject.name.strip(), "target": target,
            "target_name": r.object.name.strip(), "predicate": predicate, "chunk_id": r.chunk_id,
            "quote": r.quote, "quote_start": start, "quote_end": end, "dedup_key": key})
    return edges


def publish_document(db, settings, row, chunks, edges, assets, *, providers_used=()):
    with db.connect() as conn:
        kb = get_kb(conn, row['kb_id'], lock=True)
        current = document(conn, row['kb_id'], row['id'], lock=True)
        if current['status'] != 'processing' or current['attempt'] != row['attempt']:
            raise AppError("TASK_CANCELLED", "处理版本已失效", 409)
        if kb['embedding_profile'] != profile(settings):
            raise AppError("EMBEDDING_PROFILE_MISMATCH", "知识库编码配置与当前模型不同", 409)
        count = conn.execute("SELECT count(*) AS n FROM chunks WHERE kb_id=%s", (row['kb_id'],)).fetchone()['n']
        if count + len(chunks) > MAX_KB_CHUNKS:
            raise AppError("DOCUMENT_LIMIT", "知识库分块容量不足", 409)
        for c in chunks:
            conn.execute("""INSERT INTO chunks(id,kb_id,document_id,ordinal,content_type,heading_path,
                evidence_text,retrieval_text,title_tokens,body_tokens,source_spans,asset_id,token_count,embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (c['id'], row['kb_id'], row['id'], c['ordinal'], c['content_type'], Jsonb(c['heading_path']),
                 c['evidence_text'], c['retrieval_text'], token_text(' '.join(c['heading_path'])),
                 token_text(c['evidence_text']), Jsonb(c['source_spans']), c['asset_id'],
                 c['token_count'], c['embedding']))
        entity_ids = {}
        for edge in edges:
            for side in ('source', 'target'):
                key = edge[side]
                if key not in entity_ids:
                    entity_ids[key] = conn.execute("""INSERT INTO entities(id,kb_id,name,entity_type,normalized_name)
                        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(kb_id,entity_type,normalized_name)
                        DO UPDATE SET name=entities.name RETURNING id""",
                        (uuid4(), row['kb_id'], edge[side+'_name'], key[0], key[1])).fetchone()['id']
            conn.execute("""INSERT INTO relations(id,kb_id,source_entity_id,predicate,target_entity_id,
                evidence_chunk_id,quote,quote_start,quote_end) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""", (uuid4(), row['kb_id'], entity_ids[edge['source']],
                edge['predicate'], entity_ids[edge['target']], edge['chunk_id'], edge['quote'],
                edge['quote_start'], edge['quote_end']))
        provider = next(iter(providers_used)) if len(providers_used) == 1 else None
        model = settings.agent_model if provider == settings.agent_provider_1 else ''
        conn.execute("""UPDATE documents SET status='ready',progress=100,stage='publish',ready_at=now(),
            updated_at=now(),asset_manifest=%s,processing_meta=%s WHERE id=%s""",
            (Jsonb(assets), Jsonb({"provider": provider, "model": model, "providers_used": sorted(providers_used),
                                 "chunks": len(chunks), "relations": len(edges)}), row['id']))
        conn.execute("UPDATE knowledge_bases SET revision=revision+1 WHERE id=%s", (row['kb_id'],))


class Worker:
    def __init__(self, db, settings, encoder, agent):
        self.db, self.settings, self.encoder, self.agent = db, settings, encoder, agent
        self.stop = threading.Event()
        self.thread = None
        self.guard = None

    def start(self):
        self.guard = psycopg.connect(self.db.url, autocommit=True, connect_timeout=5)
        if not self.guard.execute("SELECT pg_try_advisory_lock(%s)", (WORKER_LOCK,)).fetchone()[0]:
            self.guard.close()
            raise RuntimeError("Another ingestion worker already owns this database")
        with self.db.connect() as conn:
            conn.execute("""UPDATE documents SET status='queued',attempt=attempt+1,stage=NULL,
                progress=0,updated_at=now() WHERE status='processing'""")
            ids = {str(r['id']) for r in conn.execute('SELECT id FROM documents')}
        root = self.settings.data_dir / 'documents'
        if root.exists():
            for path in root.iterdir():
                try:
                    doc_id = UUID(path.name)
                except ValueError:
                    continue
                if path.is_dir() and path.name not in ids:
                    cleanup_files(self.settings, [doc_id])
        self.thread = threading.Thread(target=self.run, daemon=True, name='ingestion')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            # Keep exclusive ownership until all processing has actually stopped.
            self.thread.join()
        if self.guard:
            self.guard.close()

    def claim(self):
        with self.db.connect() as conn:
            return conn.execute("""UPDATE documents SET status='processing',stage='parse',
                started_at=now(),updated_at=now() WHERE id=(SELECT id FROM documents WHERE status='queued'
                ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""").fetchone()

    def run(self):
        while not self.stop.is_set():
            try:
                # If this session is lost, its advisory lock is lost too. Never keep claiming work.
                if self.guard is not None:
                    try:
                        self.guard.execute('SELECT 1')
                    except psycopg.Error:
                        self.stop.set()
                        log.error('worker_lock_connection_lost')
                        return
                row = self.claim()
                if row:
                    self.process(row)
                    continue
            except Exception:
                log.exception("worker_cycle_failed")
            self.stop.wait(1)

    def stage(self, row, stage, progress):
        if self.stop.is_set():
            raise AppError("TASK_CANCELLED", "服务正在停止", 409)
        with self.db.connect() as conn:
            updated = conn.execute("""UPDATE documents SET stage=%s,progress=%s,updated_at=now()
                WHERE id=%s AND attempt=%s AND status='processing' RETURNING id""",
                (stage, progress, row['id'], row['attempt'])).fetchone()
        if not updated:
            raise AppError("TASK_CANCELLED", "文档已删除或处理版本失效", 409)
        log.info('document_stage', extra={'kb_id': row['kb_id'], 'document_id': row['id'],
                                         'attempt': row['attempt'], 'stage': stage})

    def process(self, row):
        folder = self.settings.data_dir / 'documents' / str(row['id']) / f"attempt-{row['attempt']}"
        committed = False
        last_check, cancelled = 0, False

        def is_cancelled():
            nonlocal last_check, cancelled
            if self.stop.is_set():
                return True
            if time.monotonic()-last_check > 1:
                with self.db.connect() as conn:
                    cancelled = not conn.execute("""SELECT id FROM documents
                        WHERE id=%s AND attempt=%s AND status='processing'""",
                        (row['id'], row['attempt'])).fetchone()
                last_check = time.monotonic()
            return cancelled

        try:
            with self.db.connect() as conn:
                if get_kb(conn, row['kb_id'])['embedding_profile'] != profile(self.settings):
                    raise AppError("EMBEDDING_PROFILE_MISMATCH", "请在当前模型配置下新建知识库并重新上传", 409)
            self.stage(row, 'parse', 5)
            blocks, assets = parse_document(self.settings.data_dir / row['source_path'], row['media_type'],
                                             folder / 'images')
            providers_used = set()
            self.stage(row, 'vision', 15)
            for index, block in enumerate(blocks):
                if block.kind == 'image':
                    context = '\n'.join(b.text for b in blocks[max(0, index-1):index+2]
                                        if b.kind == 'text')[:3000]
                    result = self.agent.describe_image(assets[block.asset_id]['path'], context,
                                                       cancelled=is_cancelled, on_provider=providers_used.add)
                    block.text = result.description
                    if result.visible_text:
                        block.text += '\n图中文字：' + '；'.join(result.visible_text)
                    if result.uncertain:
                        block.text += '\n不确定信息：' + '；'.join(result.uncertain)
            folder.mkdir(parents=True, exist_ok=True)
            (folder/'blocks.json').write_text(json.dumps([asdict(b) for b in blocks], ensure_ascii=False),
                                              encoding='utf-8')
            self.stage(row, 'chunk', 30)
            chunks = build_chunks(blocks, self.encoder)
            self.stage(row, 'embed', 45)
            for offset in range(0, len(chunks), 32):
                self.stage(row, 'embed', 45)
                batch = chunks[offset:offset+32]
                vectors = self.encoder.encode([c['retrieval_text'] for c in batch])
                for c, vector in zip(batch, vectors):
                    c['embedding'] = vector
            self.stage(row, 'graph', 60)
            edges, seen = [], set()
            for offset in range(0, len(chunks), 4):
                self.stage(row, 'graph', 60 + int(25*offset/len(chunks)))
                batch = chunks[offset:offset+4]
                payload = [{"chunk_id": str(c['id']), "type": c['content_type'], "text": c['evidence_text']}
                           for c in batch]
                result = self.agent.generate_json(
                    "抽取原文明示的实体关系。每条关系必须引用同一分块内连续逐字 quote，"
                    "完整保留原有空格、换行和标点，不得拼接不连续的句子；occurrence 从0开始。"
                    "没有关系则返回空列表，不做跨块推测。", payload, GraphResult,
                    validate=lambda value: validate_relations(value, batch), cancelled=is_cancelled,
                    on_provider=providers_used.add)
                for edge in validate_relations(result, batch):
                    if edge['dedup_key'] not in seen:
                        edges.append(edge)
                        seen.add(edge['dedup_key'])
            self.stage(row, 'publish', 95)
            for asset in assets.values():
                from pathlib import Path
                asset['path'] = Path(asset['path']).relative_to(self.settings.data_dir).as_posix()
            publish_document(self.db, self.settings, row, chunks, edges, assets, providers_used=providers_used)
            committed = True
        except Exception as exc:
            code = exc.code if isinstance(exc, AppError) else 'PROCESSING_FAILED'
            message = exc.message if isinstance(exc, AppError) else '处理失败，请通过 request/document ID 查看日志'
            log.warning("document_failed document_id=%s attempt=%s code=%s", row['id'], row['attempt'], code)
            with self.db.connect() as conn:
                conn.execute("""UPDATE documents SET status='failed',error_code=%s,error_message=%s,updated_at=now()
                    WHERE id=%s AND attempt=%s AND status='processing'""", (code, message, row['id'], row['attempt']))
        finally:
            if not committed and folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
